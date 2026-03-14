from __future__ import annotations

import os

import click

from probegen.errors import GithubApiError
from probegen.github import find_latest_workflow_run_id


@click.command("resolve-run-id")
@click.option("--repo", default=lambda: os.environ.get("GITHUB_REPOSITORY", ""), show_default="GITHUB_REPOSITORY")
@click.option("--workflow-id", default="probegen.yml", show_default=True)
@click.option("--head-sha", required=True)
@click.option("--branch")
@click.option("--event", default="pull_request", show_default=True)
@click.option("--status", default="completed", show_default=True)
@click.option("--conclusion", default="success", show_default=True)
@click.option("--token-env", default="GITHUB_TOKEN", show_default=True)
def resolve_run_id_command(
    repo: str,
    workflow_id: str,
    head_sha: str,
    branch: str | None,
    event: str,
    status: str,
    conclusion: str,
    token_env: str,
) -> None:
    token = os.environ.get(token_env, "")
    if not repo:
        click.echo("GitHub repository was not provided and GITHUB_REPOSITORY is empty", err=True)
        raise SystemExit(2)
    if not token:
        click.echo(f"Missing GitHub token in environment variable {token_env}", err=True)
        raise SystemExit(2)

    try:
        run_id = find_latest_workflow_run_id(
            repo,
            workflow_id,
            token,
            event=event,
            status=status,
            head_sha=head_sha,
            branch=branch,
            conclusion=conclusion,
        )
    except GithubApiError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    if run_id is None:
        click.echo(
            f"No workflow run found for workflow '{workflow_id}' and head SHA '{head_sha}'",
            err=True,
        )
        raise SystemExit(1)

    click.echo(str(run_id))


if __name__ == "__main__":
    resolve_run_id_command()
