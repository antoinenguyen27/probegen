from __future__ import annotations

import json
from pathlib import Path

from probegen.export import render_summary_markdown, write_run_artifacts
from probegen.github import render_pr_comment, render_results_comment
from probegen.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal


def _load_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_write_run_artifacts_creates_expected_files(tmp_path: Path) -> None:
    manifest = BehaviorChangeManifest.model_validate(
        _load_fixture("tests/fixtures/sample_manifest.json")
    )
    gaps = CoverageGapManifest.model_validate(_load_fixture("tests/fixtures/sample_gaps.json"))
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))

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
    manifest = BehaviorChangeManifest.model_validate(
        _load_fixture("tests/fixtures/sample_manifest.json")
    )
    gaps = CoverageGapManifest.model_validate(_load_fixture("tests/fixtures/sample_gaps.json"))
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))

    comment = render_pr_comment(proposal, stage1_manifest=manifest, stage2_manifest=gaps)

    assert comment.startswith("<!-- probegen-comment -->")
    assert "### Proposed Probes (2)" in comment
    assert "probe_001" not in comment
    assert "boundary_probe" in comment


def test_render_pr_comment_reports_bootstrap_mode_without_eval_corpus() -> None:
    manifest = BehaviorChangeManifest.model_validate(
        _load_fixture("tests/fixtures/sample_manifest.json")
    )
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))
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

    assert "Bootstrap mode" in comment
    assert "contains no eval cases yet" in comment
    assert "No usable eval corpus was available for comparison" in comment


def test_render_summary_markdown_lists_probes() -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))
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
