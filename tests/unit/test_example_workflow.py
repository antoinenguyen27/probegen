from __future__ import annotations

from pathlib import Path

from parity.cli.init_cmd import render_workflow_template
from parity.config import ParityConfig


def test_example_workflow_matches_rendered_template() -> None:
    root = Path(__file__).resolve().parents[2]
    config = ParityConfig.load(root / "examples" / "langgraph-agentic-rag" / "parity.yaml")
    workflow = (
        root
        / "examples"
        / "langgraph-agentic-rag"
        / ".github"
        / "workflows"
        / "parity.yml"
    ).read_text(encoding="utf-8")

    assert workflow == render_workflow_template(config)
