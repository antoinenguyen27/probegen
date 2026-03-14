from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
import fnmatch

import click

from probegen.config import ProbegenConfig
from probegen.errors import ConfigError, EventPayloadError, GitDiffError
from probegen.models.raw_change_data import ChangedArtifact, RawChangeData, content_sha256

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
    event_path_str = (env or os.environ).get("GITHUB_EVENT_PATH", "")
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


def _list_changed_files(base_branch: str, include_paths: list[str]) -> list[tuple[str, str, str | None]]:
    pathspecs = [f":(glob){pattern}" for pattern in include_paths] if include_paths else []
    output = _run_git(
        [
            "diff",
            "--name-status",
            "--find-renames",
            f"origin/{base_branch}...HEAD",
            "--",
            *pathspecs,
        ]
    )
    changed: list[tuple[str, str, str | None]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            changed.append(("rename", parts[2], parts[1]))
        else:
            path = parts[-1]
            mapping = {"A": "addition", "M": "modification", "D": "deletion"}
            changed.append((mapping.get(status[0], "modification"), path, None))
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


def _classify_artifact_path(path: str, config: ProbegenConfig) -> str:
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


def _artifact_class(path: str, config: ProbegenConfig) -> str:
    for pattern in config.guardrail_artifacts.paths:
        if fnmatch.fnmatch(path, pattern):
            return "guardrail"
    return "behavior_defining"


def _list_unchanged_behavior_artifacts(config: ProbegenConfig, changed_paths: set[str]) -> list[str]:
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
        config = ProbegenConfig.load(config_path, allow_missing=allow_missing_config)
    except ConfigError as exc:
        raise ConfigError(str(exc)) from exc

    payload = _read_event_payload(env)
    include_paths = [*config.behavior_artifacts.paths, *config.guardrail_artifacts.paths]
    changed_files = _list_changed_files(base_branch, include_paths)
    changed_artifacts: list[ChangedArtifact] = []

    for change_kind, path, original_path in changed_files:
        artifact_class = _artifact_class(path, config)
        before_path = original_path or path
        before_content = "" if change_kind == "addition" else _git_show(f"origin/{base_branch}", before_path)
        after_content = "" if change_kind == "deletion" else _git_show("HEAD", path)
        raw_diff = _git_file_diff(base_branch, path, original_path)
        changed_artifacts.append(
            ChangedArtifact(
                path=path,
                artifact_class=artifact_class,
                artifact_type=_classify_artifact_path(path, config),
                change_kind=change_kind,  # type: ignore[arg-type]
                before_content=before_content,
                after_content=after_content,
                raw_diff=raw_diff,
                before_sha=content_sha256(before_content),
                after_sha=content_sha256(after_content),
            )
        )

    changed_paths = {artifact.path for artifact in changed_artifacts if artifact.artifact_class == "behavior_defining"}
    pull_request = payload["pull_request"]
    data = RawChangeData(
        pr_number=pr_number,
        pr_title=pull_request.get("title") or "",
        pr_body=pull_request.get("body") or "",
        pr_labels=[label["name"] for label in pull_request.get("labels", []) if "name" in label],
        base_branch=base_branch,
        head_sha=pull_request.get("head", {}).get("sha", ""),
        repo_full_name=payload["repository"].get("full_name", ""),
        changed_artifacts=changed_artifacts,
        unchanged_behavior_artifacts=_list_unchanged_behavior_artifacts(config, changed_paths),
        has_changes=bool(changed_artifacts),
        artifact_count=len(changed_artifacts),
    )
    return data


@click.command("get-behavior-diff")
@click.option("--base-branch", required=True)
@click.option("--pr-number", required=True, type=int)
@click.option("--config", "config_path", default="probegen.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
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
