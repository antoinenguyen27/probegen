from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

import click

from parity.config import FIXED_APPROVAL_LABEL, ParityConfig
from parity.errors import ConfigError


def _git_ls_files(cwd: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


@click.command("doctor")
@click.option("--config", "config_path", default="parity.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--ci", is_flag=True, help="Run additional CI-specific checks (requires GITHUB_TOKEN).")
def doctor_command(config_path: Path, ci: bool) -> None:
    """Verify your Parity setup and report any issues."""
    checks: list[tuple[bool, str]] = []
    root = Path.cwd()

    # Check 1: parity.yaml exists
    config_exists = config_path.exists()
    checks.append((config_exists, f"parity.yaml found at {config_path}"))
    if not config_exists:
        click.echo(_format_checks(checks))
        click.echo(f"\nRun `parity init` to create parity.yaml.")
        return

    # Check 2: config is valid
    config: ParityConfig | None = None
    try:
        config = ParityConfig.load(config_path, allow_missing=False)
        checks.append((True, "parity.yaml is valid"))
    except ConfigError as exc:
        checks.append((False, f"parity.yaml has errors: {exc}"))

    if config is not None:
        for warning in config.compatibility_warnings():
            checks.append((False, warning))

        # Check 3: ANTHROPIC_API_KEY
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        checks.append((bool(anthropic_key), "ANTHROPIC_API_KEY is set"))

        # Check 4: Platform-specific keys
        if config.platforms.langsmith:
            key_name = config.platforms.langsmith.api_key_env
            checks.append((bool(os.environ.get(key_name)), f"{key_name} is set (langsmith)"))

        if config.platforms.braintrust:
            key_name = config.platforms.braintrust.api_key_env
            checks.append((bool(os.environ.get(key_name)), f"{key_name} is set (braintrust)"))

        if config.platforms.arize_phoenix:
            key_name = config.platforms.arize_phoenix.api_key_env
            checks.append((bool(os.environ.get(key_name)), f"{key_name} is set (arize_phoenix)"))

        # Check 5: OPENAI_API_KEY if eval rules are configured
        if config.evals.rules:
            openai_key = os.environ.get("OPENAI_API_KEY", "")
            checks.append((bool(openai_key), "OPENAI_API_KEY is set (required for coverage-aware mode)"))

        # Check 6: Hint pattern matches
        tracked_files = _git_ls_files(root)
        if tracked_files:
            all_patterns = [*config.behavior_artifacts.paths, *config.guardrail_artifacts.paths]
            if all_patterns:
                for pattern in all_patterns:
                    matched = [
                        f for f in tracked_files
                        if fnmatch.fnmatch(f, pattern)
                        and not any(fnmatch.fnmatch(f, ex) for ex in config.behavior_artifacts.exclude)
                    ]
                    checks.append((
                        bool(matched),
                        f"Pattern '{pattern}' matches {len(matched)} tracked file(s)",
                    ))
            else:
                checks.append((False, "No hint patterns configured in behavior_artifacts or guardrail_artifacts"))

        # Check 7: workflow file
        workflow_path = root / ".github" / "workflows" / "parity.yml"
        checks.append((workflow_path.exists(), f"Workflow file {workflow_path.relative_to(root)} exists"))

        # Check 8: context/ directory key files
        context_files = [
            config.context.product,
            config.context.users,
            config.context.interactions,
            config.context.good_examples,
            config.context.bad_examples,
        ]
        for rel_path in context_files:
            full_path = root / rel_path
            non_empty = full_path.exists() and full_path.stat().st_size > 0
            checks.append((non_empty, f"Context file {rel_path} exists and is non-empty"))

        # Check 9: CI label check
        if ci:
            token = os.environ.get("GITHUB_TOKEN", "")
            repo = os.environ.get("GITHUB_REPOSITORY", "")
            if token and repo:
                label_ok = _check_github_label(repo, token, FIXED_APPROVAL_LABEL)
                checks.append((label_ok, f"GitHub label '{FIXED_APPROVAL_LABEL}' exists in {repo}"))
            else:
                checks.append((False, "GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping label check"))

    click.echo(_format_checks(checks))
    passed = sum(1 for ok, _ in checks if ok)
    total = len(checks)
    click.echo(f"\n{passed}/{total} checks passed.")


def _check_github_label(repo: str, token: str, label_name: str) -> bool:
    try:
        import httpx
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/labels/{label_name}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
        return response.status_code == 200
    except Exception:
        return False


def _format_checks(checks: list[tuple[bool, str]]) -> str:
    lines = []
    for ok, message in checks:
        symbol = "✓" if ok else "✗"
        lines.append(f"  {symbol} {message}")
    return "\n".join(lines)


if __name__ == "__main__":
    doctor_command()
