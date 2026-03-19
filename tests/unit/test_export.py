from __future__ import annotations

import json
from pathlib import Path

from probegen.export import export_deepeval_stub, render_summary_markdown, write_run_artifacts
from probegen.github import render_pr_comment, render_results_comment
from probegen.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal

_FIXTURES = Path(__file__).parents[2] / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_write_run_artifacts_creates_expected_files(tmp_path: Path) -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    gaps = CoverageGapManifest.model_validate(_load_fixture("sample_gaps.json"))
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))

    outputs = write_run_artifacts(
        run_dir=tmp_path / ".probegen" / "runs" / proposal.commit_sha,
        stage1_manifest=manifest,
        stage2_manifest=gaps,
        proposal=proposal,
        metadata={"stage": 3},
    )

    assert outputs["proposal"].exists()
    assert outputs["summary"].exists()
    assert outputs["metadata"].exists()
    assert outputs["test_file"].exists()
    assert outputs["prompt_file"].exists()
    assert "Probe Summary" in outputs["summary"].read_text(encoding="utf-8")


def test_render_pr_comment_includes_marker_and_probe_table() -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    gaps = CoverageGapManifest.model_validate(_load_fixture("sample_gaps.json"))
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))

    comment = render_pr_comment(proposal, stage1_manifest=manifest, stage2_manifest=gaps)

    assert comment.startswith("<!-- probegen-comment -->")
    assert "### Proposed Probes (2)" in comment
    assert "probe_001" in comment  # Now included in collapsible details
    assert "boundary_probe" in comment
    assert "<details>" in comment  # Collapsible sections present
    assert "Full Details" in comment  # Instruction text present


def test_render_pr_comment_reports_bootstrap_mode_without_eval_corpus() -> None:
    manifest = BehaviorChangeManifest.model_validate(_load_fixture("sample_manifest.json"))
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))
    bootstrap_gaps = CoverageGapManifest.model_validate(
        {
            "run_id": "stage2-bootstrap",
            "stage1_run_id": "stage1-run",
            "timestamp": "2026-03-14T10:01:00Z",
            "unmapped_artifacts": [],
            "coverage_summary": {
                "total_relevant_cases": 0,
                "cases_covering_changed_behavior": 0,
                "coverage_ratio": 0.0,
                "platform": "langsmith",
                "dataset": "citation-agent-evals",
                "mode": "bootstrap",
                "corpus_status": "empty",
                "bootstrap_reason": "The mapped dataset exists but contains no eval cases yet.",
            },
            "gaps": [],
        }
    )

    comment = render_pr_comment(proposal, stage1_manifest=manifest, stage2_manifest=bootstrap_gaps)

    assert "Starter mode" in comment
    assert "contains no eval cases yet" in comment
    assert "grounded in your diff and product context" in comment


def test_render_summary_markdown_lists_probes() -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))
    summary = render_summary_markdown(proposal)

    assert "# Probegen Probe Summary" in summary
    assert "probe_001" in summary


def test_render_results_comment_handles_zero_writes() -> None:
    comment = render_results_comment(
        dataset_name="langsmith:demo-dataset",
        total_written=0,
        failures=[{"probe_id": "n/a", "probe_type": "n/a", "failure": "No write target found"}],
    )

    assert "No probes were written" in comment
    assert "Targets attempted" in comment


def test_write_run_artifacts_includes_deepeval_file(tmp_path: Path) -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))

    outputs = write_run_artifacts(
        run_dir=tmp_path / "runs" / proposal.commit_sha,
        proposal=proposal,
        metadata={},
    )

    assert "deepeval" in outputs
    assert outputs["deepeval"].exists()
    content = outputs["deepeval"].read_text(encoding="utf-8")
    assert "LLMTestCase" in content
    assert "CASES" in content


def test_export_deepeval_stub_contains_one_entry_per_probe(tmp_path: Path) -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))

    path = export_deepeval_stub(proposal, output_path=tmp_path / "probes_deepeval.py")

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    # sample_proposal has 2 probes → 2 LLMTestCase calls
    assert content.count("LLMTestCase(") == len(proposal.probes)


def test_render_results_comment_with_pass_fail_counts() -> None:
    comment = render_results_comment(
        dataset_name="langsmith:evals",
        total_written=3,
        passed=2,
        failed=1,
    )

    assert "3 probes written to" in comment
    assert "2 passed, 1 failed" in comment
