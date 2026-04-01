from __future__ import annotations

import json
from pathlib import Path

from parity.export import export_native_render_artifacts, render_summary_markdown, write_run_artifacts
from parity.github import render_pr_comment, render_results_comment
from parity.models import (
    EvalAnalysisManifest,
    BehaviorChangeManifest,
    EvalProposalManifest,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_write_run_artifacts_creates_expected_files(tmp_path: Path) -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    analysis = EvalAnalysisManifest.model_validate(_load_fixture("sample_analysis.json"))
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))

    outputs = write_run_artifacts(
        run_dir=tmp_path / ".parity" / "runs" / proposal.commit_sha,
        stage1_manifest=manifest,
        stage2_manifest=analysis,
        proposal=proposal,
        metadata={"stage": 3},
    )

    assert outputs["proposal"].exists()
    assert outputs["summary"].exists()
    assert outputs["metadata"].exists()
    assert outputs["render_artifact_0"].exists()
    assert "Eval Proposal Summary" in outputs["summary"].read_text(encoding="utf-8")


def test_render_pr_comment_includes_marker_and_write_status_table() -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    analysis = EvalAnalysisManifest.model_validate(_load_fixture("sample_analysis.json"))
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))

    comment = render_pr_comment(
        proposal,
        stage1_manifest=manifest,
        stage2_manifest=analysis,
    )

    assert comment.startswith("<!-- parity-comment -->")
    assert "### Proposed Evals (2)" in comment
    assert "intent_001" in comment
    assert "native_ready" in comment
    assert "row_local" in comment
    assert "<details>" in comment


def test_render_pr_comment_distinguishes_degraded_analysis_from_confirmed_no_target() -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    analysis = EvalAnalysisManifest.model_validate(
        {
            "run_id": "stage2",
            "stage1_run_id": "stage1",
            "timestamp": "2026-03-31T00:00:00Z",
            "analysis_status": "degraded",
            "degradation_reason": "Permission-denied MCP calls exhausted the Stage 2 budget.",
            "unresolved_artifacts": ["app/graph.py"],
            "resolved_targets": [],
            "coverage_by_target": [],
            "gaps": [],
        }
    )
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))

    comment = render_pr_comment(
        proposal,
        stage1_manifest=manifest,
        stage2_manifest=analysis,
    )

    assert "Stage 2 analysis degraded before full native target resolution completed." in comment
    assert "remained unresolved when analysis degraded" in comment
    assert "No usable native eval target was discovered for `app/graph.py`" not in comment


def test_render_pr_comment_includes_proposal_warnings() -> None:
    proposal = EvalProposalManifest.model_validate(
        {
            **_load_fixture("sample_proposal.json"),
            "warnings": ["Target `braintrust::demo` is missing Braintrust project metadata."],
        }
    )

    comment = render_pr_comment(proposal)

    assert "### Proposal Warnings" in comment
    assert "missing Braintrust project metadata" in comment


def test_render_summary_markdown_lists_intents() -> None:
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))
    summary = render_summary_markdown(proposal)

    assert "# Parity Eval Proposal Summary" in summary
    assert "intent_001" in summary
    assert "Evaluator linkage: row_local" in summary


def test_render_results_comment_handles_zero_writes() -> None:
    comment = render_results_comment(
        targets="promptfoo:evals/promptfooconfig.yaml",
        total_written=0,
        failures=["No native-ready renderings were available."],
    )

    assert "No evals were written" in comment
    assert "Targets attempted" in comment


def test_export_native_render_artifacts_writes_promptfoo_file(tmp_path: Path) -> None:
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))

    artifacts = export_native_render_artifacts(proposal, output_dir=tmp_path / "artifacts")

    assert len(artifacts) == 1
    assert artifacts[0].artifact_kind == "promptfoo_config"
    assert Path(artifacts[0].path).exists()


def test_render_results_comment_lists_skipped_and_unsupported_targets() -> None:
    comment = render_results_comment(
        targets="promptfoo:evals/promptfooconfig.yaml",
        total_written=3,
        skipped_review_only=["bootstrap:prompts/foo.md"],
        unsupported_targets=["braintrust:legacy/unsupported"],
    )

    assert "3 evals written to" in comment
    assert "Skipped review-only targets" in comment
    assert "Unsupported targets" in comment
