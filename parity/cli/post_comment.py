from __future__ import annotations

import json
import os
from pathlib import Path

import click

from parity.errors import GithubApiError
from parity.github import find_existing_comment, post_pr_comment, render_pr_comment, update_pr_comment
from parity.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal

NO_CHANGES_COMMENT = """<!-- parity-comment -->
## Parity: No Behavioral Changes Detected

This PR does not modify any behavior-defining artifacts (prompts, instructions, guardrails, tool descriptions). No evals were generated.

If you believe behavioral artifacts were changed and Parity missed them, check your `behavior_artifacts` hint patterns in `parity.yaml`."""


def _load_optional_manifest(directory: Path, candidates: list[str], model) -> object | None:
    for name in candidates:
        path = directory / name
        if path.exists():
            return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return None


@click.command("post-comment", help="Post eval results as a PR comment on GitHub.")
@click.option("--proposal", "proposal_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--no-changes", "no_changes", is_flag=True, help="Post a no-behavioral-changes comment.")
@click.option("--pr-number", required=True, type=int)
@click.option("--repo", default=lambda: os.environ.get("GITHUB_REPOSITORY", ""), show_default="GITHUB_REPOSITORY")
@click.option("--token", default=lambda: os.environ.get("GITHUB_TOKEN", ""), show_default="GITHUB_TOKEN")
def post_comment_command(
    proposal_path: Path | None,
    no_changes: bool,
    pr_number: int,
    repo: str,
    token: str,
) -> None:
    if no_changes:
        try:
            existing_comment_id = find_existing_comment(pr_number, repo, token)
            if existing_comment_id is not None:
                update_pr_comment(existing_comment_id, NO_CHANGES_COMMENT, repo, token)
            else:
                post_pr_comment(pr_number, NO_CHANGES_COMMENT, repo, token)
        except GithubApiError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(1) from exc
        return

    if proposal_path is None:
        click.echo("--proposal is required unless --no-changes is set", err=True)
        raise SystemExit(2)

    try:
        proposal = ProbeProposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
    except Exception as exc:
        click.echo(f"Invalid proposal JSON: {exc}", err=True)
        raise SystemExit(2) from exc

    stage1_manifest = _load_optional_manifest(
        proposal_path.parent,
        ["stage1.json", "BehaviorChangeManifest.json"],
        BehaviorChangeManifest,
    )
    stage2_manifest = _load_optional_manifest(
        proposal_path.parent,
        ["stage2.json", "CoverageGapManifest.json"],
        CoverageGapManifest,
    )

    body = render_pr_comment(
        proposal,
        stage1_manifest=stage1_manifest,
        stage2_manifest=stage2_manifest,
    )
    try:
        existing_comment_id = find_existing_comment(pr_number, repo, token)
        if existing_comment_id is not None:
            updated_body = render_pr_comment(
                proposal,
                stage1_manifest=stage1_manifest,
                stage2_manifest=stage2_manifest,
                updated_for_commit=proposal.commit_sha,
            )
            update_pr_comment(existing_comment_id, updated_body, repo, token)
        else:
            post_pr_comment(pr_number, body, repo, token)
    except GithubApiError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    post_comment_command()
