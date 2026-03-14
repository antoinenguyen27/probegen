from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_get_behavior_diff_against_real_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Probegen Test")
    _git(repo, "config", "user.email", "probegen@example.com")

    prompts_dir = repo / "prompts" / "citation_agent"
    prompts_dir.mkdir(parents=True)
    prompt_path = prompts_dir / "system_prompt.md"
    prompt_path.write_text("You are a helpful assistant.\n", encoding="utf-8")

    config_path = repo / "probegen.yaml"
    config_path.write_text(
        """
version: 1
behavior_artifacts:
  paths:
    - "prompts/**"
guardrail_artifacts:
  paths:
    - "guardrails/**"
""".strip(),
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    head_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", head_sha)

    prompt_path.write_text(
        "You are a helpful assistant.\nAlways cite sources when answering factual questions.\n",
        encoding="utf-8",
    )
    _git(repo, "add", str(prompt_path.relative_to(repo)))
    _git(repo, "commit", "-m", "change prompt")
    changed_head_sha = _git(repo, "rev-parse", "HEAD")

    event_payload = {
        "pull_request": {
            "number": 142,
            "title": "Add citation requirement",
            "body": "Adds citations to factual answers.",
            "base": {"ref": "main"},
            "head": {"sha": changed_head_sha},
            "labels": [{"name": "prompts"}],
            "user": {"login": "test-user"},
        },
        "repository": {"full_name": "org/repo"},
    }
    event_path = repo / "event.json"
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")

    env = os.environ.copy()
    env["GITHUB_EVENT_PATH"] = str(event_path)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "probegen.cli.get_behavior_diff",
            "--base-branch",
            "main",
            "--pr-number",
            "142",
            "--config",
            str(config_path),
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["has_changes"] is True
    assert payload["artifact_count"] == 1
    artifact = payload["changed_artifacts"][0]
    assert artifact["path"] == "prompts/citation_agent/system_prompt.md"
    assert artifact["artifact_class"] == "behavior_defining"
    assert artifact["artifact_type"] == "system_prompt"
    assert "Always cite sources" in artifact["after_content"]
