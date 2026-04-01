from __future__ import annotations

import json
import os
from dataclasses import asdict
from dataclasses import dataclass, field
from pathlib import Path

import click

from parity.config import ParityConfig
from parity.errors import GithubApiError
from parity.github import post_pr_comment, render_results_comment
from parity.integrations.braintrust import BraintrustWriter
from parity.integrations.langsmith import LangSmithWriter
from parity.integrations.phoenix import PhoenixWriter
from parity.integrations.promptfoo import PromptfooWriter
from parity.models import EvalProposalManifest, NativeEvalRendering


@dataclass
class EvalWriteOutcome:
    exit_code: int
    total_written: int = 0
    attempted_targets: list[str] = field(default_factory=list)
    written_targets: list[str] = field(default_factory=list)
    skipped_review_only: list[str] = field(default_factory=list)
    unsupported_targets: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def messages(self) -> list[str]:
        return self.failures


def _serialize_outcome(outcome: EvalWriteOutcome) -> dict[str, object]:
    return asdict(outcome)


def _load_outcome(path: Path) -> EvalWriteOutcome:
    return EvalWriteOutcome(**json.loads(path.read_text(encoding="utf-8")))


def _write_outcome(path: Path, outcome: EvalWriteOutcome) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize_outcome(outcome), indent=2), encoding="utf-8")


def _target_label(platform: str, target_name: str, project: str | None) -> str:
    if platform == "braintrust" and project:
        return f"{platform}:{project}/{target_name}"
    return f"{platform}:{target_name}"


def _resolve_promptfoo_target(locator: str, *, config: ParityConfig, repo_root: Path) -> Path:
    configured_target = locator or (
        config.platforms.promptfoo.config_path if config.platforms.promptfoo else "promptfooconfig.yaml"
    )
    resolved = config.resolve_path(configured_target, repo_root).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"Promptfoo write target must stay within the repository root: {configured_target}") from exc
    return resolved


def _renderings_to_write(
    proposal: EvalProposalManifest,
    *,
    config: ParityConfig,
) -> tuple[dict[str, list[NativeEvalRendering]], list[str], list[str]]:
    target_lookup = {target.target_id: target for target in proposal.targets}
    grouped: dict[str, list[NativeEvalRendering]] = {}
    review_only: list[str] = []
    unsupported: list[str] = []

    for rendering in proposal.renderings:
        target = target_lookup.get(rendering.target_id)
        if target is None:
            unsupported.append(f"{rendering.target_id}: missing target profile")
            continue
        label = _target_label(target.platform, target.target_name, target.project)
        if rendering.write_status == "review_only":
            if label not in review_only:
                review_only.append(label)
            continue
        if rendering.write_status == "unsupported":
            if label not in unsupported:
                unsupported.append(label)
            continue
        if config.evals.write.require_native_rendering and rendering.write_status != "native_ready":
            continue
        if rendering.render_confidence < config.evals.write.min_render_confidence:
            if label not in review_only:
                review_only.append(label)
            continue
        grouped.setdefault(rendering.target_id, []).append(rendering)
    return grouped, review_only, unsupported


def _post_results_comment(
    outcome: EvalWriteOutcome,
    *,
    pr_number: int,
    repo: str,
    token: str,
    run_id: str | None = None,
) -> None:
    if outcome.total_written <= 0 and not outcome.failures:
        return
    body = render_results_comment(
        targets=", ".join(outcome.written_targets or outcome.attempted_targets) or None,
        total_written=outcome.total_written,
        skipped_review_only=outcome.skipped_review_only,
        unsupported_targets=outcome.unsupported_targets,
        failures=outcome.failures or None,
        run_id=run_id,
    )
    post_pr_comment(pr_number, body, repo, token)


def write_evals_from_proposal(
    proposal: EvalProposalManifest,
    *,
    config: ParityConfig,
    repo_root: Path | None = None,
) -> EvalWriteOutcome:
    resolved_repo_root = (repo_root or Path.cwd()).resolve()
    target_lookup = {target.target_id: target for target in proposal.targets}
    grouped, review_only, unsupported = _renderings_to_write(proposal, config=config)
    attempted_targets = sorted(
        {
            _target_label(target_lookup[target_id].platform, target_lookup[target_id].target_name, target_lookup[target_id].project)
            for target_id in grouped
            if target_id in target_lookup
        }
    )
    written_targets: list[str] = []
    failures: list[str] = []
    total_written = 0

    for target_id, renderings in grouped.items():
        target = target_lookup[target_id]
        label = _target_label(target.platform, target.target_name, target.project)
        try:
            if target.platform == "langsmith":
                LangSmithWriter(api_key=os.environ.get("LANGSMITH_API_KEY")).create_examples_from_renderings(
                    renderings,
                    dataset_name=target.target_name,
                    dataset_id=target.dataset_id,
                    source_pr=proposal.pr_number,
                    source_commit=proposal.commit_sha,
                )
            elif target.platform == "braintrust":
                if not (target.project or "").strip():
                    raise ValueError("Braintrust write target is missing required `project` metadata.")
                BraintrustWriter(
                    api_key=os.environ.get("BRAINTRUST_API_KEY"),
                    org_name=config.platforms.braintrust.org if config.platforms.braintrust else None,
                ).create_examples_from_renderings(
                    renderings,
                    project=target.project or "",
                    dataset_name=target.target_name,
                )
            elif target.platform == "arize_phoenix":
                PhoenixWriter(
                    base_url=config.platforms.arize_phoenix.base_url if config.platforms.arize_phoenix else None,
                    api_key=os.environ.get("PHOENIX_API_KEY"),
                ).create_examples_from_renderings(renderings, dataset_name=target.target_name)
            elif target.platform == "promptfoo":
                resolved_target = _resolve_promptfoo_target(target.locator, config=config, repo_root=resolved_repo_root)
                PromptfooWriter().write_renderings(
                    renderings,
                    test_file=resolved_target,
                    artifact_path=", ".join(target.artifact_paths) if target.artifact_paths else None,
                    pr_number=proposal.pr_number,
                    commit_sha=proposal.commit_sha,
                )
            else:
                failures.append(f"{label}: unsupported platform `{target.platform}`")
                continue
            total_written += len(renderings)
            written_targets.append(label)
        except Exception as exc:
            failures.append(f"{label}: {exc}")

    if failures and total_written == 0:
        return EvalWriteOutcome(
            exit_code=2,
            total_written=0,
            attempted_targets=attempted_targets,
            written_targets=[],
            skipped_review_only=review_only,
            unsupported_targets=unsupported,
            failures=failures,
        )
    if failures:
        return EvalWriteOutcome(
            exit_code=1,
            total_written=total_written,
            attempted_targets=attempted_targets,
            written_targets=written_targets,
            skipped_review_only=review_only,
            unsupported_targets=unsupported,
            failures=failures,
        )
    return EvalWriteOutcome(
        exit_code=0,
        total_written=total_written,
        attempted_targets=attempted_targets,
        written_targets=written_targets,
        skipped_review_only=review_only,
        unsupported_targets=unsupported,
        failures=[],
    )


@click.command("write-evals", help="Write native-ready evals to the discovered eval targets.")
@click.option("--proposal", "proposal_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--config", "config_path", default="parity.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--outcome-output", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--skip-comment", is_flag=True, help="Skip posting the merged-PR results comment from this process.")
def write_evals_command(
    proposal_path: Path,
    config_path: Path,
    outcome_output: Path | None,
    skip_comment: bool,
) -> None:
    try:
        proposal = EvalProposalManifest.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
        config = ParityConfig.load(config_path, allow_missing=True)
        for warning in config.compatibility_warnings():
            click.echo(f"parity: warning: {warning}", err=True)
        outcome = write_evals_from_proposal(
            proposal,
            config=config,
            repo_root=Path.cwd().resolve(),
        )
    except Exception as exc:
        outcome = EvalWriteOutcome(exit_code=2, failures=[str(exc)])

    if outcome_output is not None:
        _write_outcome(outcome_output, outcome)

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    pr_number = os.environ.get("PR_NUMBER")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not skip_comment and repo and token and pr_number:
        try:
            _post_results_comment(
                outcome,
                pr_number=int(pr_number),
                repo=repo,
                token=token,
                run_id=run_id,
            )
        except GithubApiError:
            pass

    for message in outcome.messages:
        click.echo(message, err=True)
    raise SystemExit(outcome.exit_code)


@click.command("post-write-comment", help="Post merged-PR writeback results from a saved outcome file.")
@click.option("--outcome", "outcome_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--pr-number", type=int)
@click.option("--repo", default=lambda: os.environ.get("GITHUB_REPOSITORY", ""), show_default="GITHUB_REPOSITORY")
@click.option("--token", default=lambda: os.environ.get("GITHUB_TOKEN", ""), show_default="GITHUB_TOKEN")
@click.option("--run-id", default=lambda: os.environ.get("GITHUB_RUN_ID", ""), show_default="GITHUB_RUN_ID")
def post_write_comment_command(
    outcome_path: Path,
    pr_number: int | None,
    repo: str,
    token: str,
    run_id: str,
) -> None:
    outcome = _load_outcome(outcome_path)
    resolved_pr_number = pr_number
    if resolved_pr_number is None:
        raw_pr_number = os.environ.get("PR_NUMBER", "").strip()
        resolved_pr_number = int(raw_pr_number) if raw_pr_number else None

    if repo and token and resolved_pr_number is not None:
        try:
            _post_results_comment(
                outcome,
                pr_number=resolved_pr_number,
                repo=repo,
                token=token,
                run_id=run_id or None,
            )
        except GithubApiError as exc:
            click.echo(str(exc), err=True)

    for message in outcome.messages:
        click.echo(message, err=True)
    raise SystemExit(outcome.exit_code)


def main() -> None:
    write_evals_command()


if __name__ == "__main__":
    main()
