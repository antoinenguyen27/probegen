from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
import fnmatch

import click

from parity.config import ParityConfig
from parity.errors import ConfigError, EventPayloadError, GitDiffError
from parity.models.raw_change_data import (
    ChangedArtifact,
    ChangedFile,
    HintPatterns,
    RawChangeData,
    content_sha256,
)

IGNORED_GIT_SHOW_ERRORS = (
    "does not exist",
    "exists on disk, but not in",
    "path not in the working tree",
)


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GitDiffError(exc.stderr.strip() or exc.stdout.strip() or "git command failed") from exc
    return completed.stdout


def _read_event_payload(env: dict[str, str] | None = None) -> dict[str, Any]:
    event_path_str = (os.environ if env is None else env).get("GITHUB_EVENT_PATH", "")
    if not event_path_str:
        raise EventPayloadError("GITHUB_EVENT_PATH is not set")
    event_path = Path(event_path_str)
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise EventPayloadError(f"Malformed GitHub event payload: {exc}") from exc
    if "pull_request" not in payload or "repository" not in payload:
        raise EventPayloadError("GitHub event payload is missing pull_request or repository data")
    return payload


def _list_all_changed_files(base_branch: str) -> list[ChangedFile]:
    output = _run_git(
        [
            "diff",
            "--name-status",
            "--find-renames",
            f"origin/{base_branch}...HEAD",
        ]
    )
    changed: list[ChangedFile] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            changed.append(ChangedFile(path=parts[2], change_kind="rename", renamed_from=parts[1]))
        else:
            path = parts[-1]
            mapping = {"A": "addition", "M": "modification", "D": "deletion"}
            changed.append(ChangedFile(path=path, change_kind=mapping.get(status[0], "modification")))
    return changed


def _git_show(ref: str, path: str) -> str:
    try:
        return _run_git(["show", f"{ref}:{path}"])
    except GitDiffError as exc:
        if any(fragment in str(exc) for fragment in IGNORED_GIT_SHOW_ERRORS):
            return ""
        raise


def _git_file_diff(base_branch: str, path: str, original_path: str | None = None) -> str:
    diff_targets = [path] if original_path is None else [original_path, path]
    return _run_git(
        [
            "diff",
            "--unified=5",
            f"origin/{base_branch}...HEAD",
            "--",
            *diff_targets,
        ]
    )


def _classify_artifact_path(path: str, config: ParityConfig) -> str:
    lowered = path.lower()
    if any(part in lowered for part in ("judge", "rubric")):
        return "llm_judge"
    if "tool_description" in lowered:
        return "tool_description"
    if "planner" in lowered:
        return "planner_prompt"
    if "retrieval" in lowered:
        return "retrieval_instruction"
    if "output_schema" in lowered:
        return "output_schema"
    if "input_classifier" in lowered:
        return "input_classifier"
    if "output_classifier" in lowered:
        return "output_classifier"
    if "validator" in lowered and "tool" in lowered:
        return "tool_validator"
    if "safety" in lowered:
        return "safety_classifier"
    if "retry" in lowered:
        return "retry_policy"
    if "schema" in lowered and "validator" in lowered:
        return "schema_validator"
    if "fallback" in lowered:
        return "fallback_prompt"
    if any(token in lowered for token in ("prompt", "instruction", "system")):
        return "system_prompt"
    return "unknown"


def _artifact_class(path: str, config: ParityConfig) -> str:
    for pattern in config.guardrail_artifacts.paths:
        if fnmatch.fnmatch(path, pattern):
            return "guardrail"
    return "behavior_defining"


def _matches_hint_patterns(path: str, config: ParityConfig) -> bool:
    all_patterns = [*config.behavior_artifacts.paths, *config.guardrail_artifacts.paths]
    if not all_patterns:
        return False
    for pattern in config.behavior_artifacts.exclude:
        if fnmatch.fnmatch(path, pattern):
            return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in all_patterns)


def _list_unchanged_hint_matches(config: ParityConfig, changed_paths: set[str]) -> list[str]:
    if not config.behavior_artifacts.paths:
        return []
    tracked = _run_git(["ls-files"]).splitlines()
    unchanged = []
    for path in tracked:
        if path in changed_paths:
            continue
        if any(fnmatch.fnmatch(path, pattern) for pattern in config.behavior_artifacts.paths):
            if any(fnmatch.fnmatch(path, pattern) for pattern in config.behavior_artifacts.exclude):
                continue
            unchanged.append(path)
    return sorted(unchanged)


def build_raw_change_data(
    base_branch: str,
    pr_number: int,
    config_path: Path,
    *,
    env: dict[str, str] | None = None,
    allow_missing_config: bool = False,
) -> RawChangeData:
    try:
        config = ParityConfig.load(config_path, allow_missing=allow_missing_config)
    except ConfigError as exc:
        raise ConfigError(str(exc)) from exc
    for warning in config.compatibility_warnings():
        click.echo(f"parity: warning: {warning}", err=True)

    payload = _read_event_payload(env)
    all_changed_files = _list_all_changed_files(base_branch)
    click.echo(f"[parity] {len(all_changed_files)} file(s) changed in this PR", err=True)

    hint_matched_artifacts: list[ChangedArtifact] = []

    for changed_file in all_changed_files:
        if not _matches_hint_patterns(changed_file.path, config):
            continue
        change_kind = changed_file.change_kind
        original_path = changed_file.renamed_from
        artifact_class = _artifact_class(changed_file.path, config)
        before_path = original_path or changed_file.path
        before_content = "" if change_kind == "addition" else _git_show(f"origin/{base_branch}", before_path)
        after_content = "" if change_kind == "deletion" else _git_show("HEAD", changed_file.path)
        raw_diff = _git_file_diff(base_branch, changed_file.path, original_path)
        hint_matched_artifacts.append(
            ChangedArtifact(
                path=changed_file.path,
                artifact_class=artifact_class,
                artifact_type=_classify_artifact_path(changed_file.path, config),
                change_kind=change_kind,  # type: ignore[arg-type]
                before_content=before_content,
                after_content=after_content,
                raw_diff=raw_diff,
                before_sha=content_sha256(before_content),
                after_sha=content_sha256(after_content),
            )
        )

    click.echo(
        f"[parity] {len(hint_matched_artifacts)} hint-matched artifact(s) "
        f"(of {len(all_changed_files)} changed file(s))",
        err=True,
    )
    for artifact in hint_matched_artifacts:
        click.echo(f"  → {artifact.change_kind}: {artifact.path} ({artifact.artifact_type})", err=True)

    hint_patterns = HintPatterns(
        behavior_paths=list(config.behavior_artifacts.paths),
        guardrail_paths=list(config.guardrail_artifacts.paths),
        behavior_python_patterns=list(config.behavior_artifacts.python_patterns),
        guardrail_python_patterns=list(config.guardrail_artifacts.python_patterns),
    )

    changed_paths = {f.path for f in all_changed_files}
    unchanged_hint_matches = _list_unchanged_hint_matches(config, changed_paths)
    if unchanged_hint_matches:
        click.echo(
            f"[parity] {len(unchanged_hint_matches)} unchanged hint-matched file(s) passed as context",
            err=True,
        )

    pull_request = payload["pull_request"]
    data = RawChangeData(
        pr_number=pr_number,
        pr_title=pull_request.get("title") or "",
        pr_body=pull_request.get("body") or "",
        pr_labels=[label["name"] for label in pull_request.get("labels", []) if "name" in label],
        base_branch=base_branch,
        head_sha=pull_request.get("head", {}).get("sha", ""),
        repo_full_name=payload["repository"].get("full_name", ""),
        all_changed_files=all_changed_files,
        hint_matched_artifacts=hint_matched_artifacts,
        hint_patterns=hint_patterns,
        unchanged_hint_matches=unchanged_hint_matches,
        has_changes=bool(all_changed_files),
        artifact_count=len(all_changed_files),
    )
    return data


@click.command("get-behavior-diff", help="Extract behavioral changes from a PR diff.")
@click.option("--base-branch", required=True)
@click.option("--pr-number", required=True, type=int)
@click.option("--config", "config_path", default="parity.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def get_behavior_diff_command(base_branch: str, pr_number: int, config_path: Path) -> None:
    try:
        raw_change_data = build_raw_change_data(base_branch, pr_number, config_path)
    except GitDiffError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    except EventPayloadError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(2) from exc
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(3) from exc

    click.echo(raw_change_data.model_dump_json(indent=2))


if __name__ == "__main__":
    get_behavior_diff_command()
