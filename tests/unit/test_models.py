from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from parity.models import (
    BehaviorChangeManifest,
    CoverageGapManifest,
    EvalCase,
    ProbeProposal,
    RawChangeData,
)


def test_raw_change_data_validates_and_computes_counts() -> None:
    model = RawChangeData.model_validate(
        {
            "pr_number": 142,
            "pr_title": "Add citation requirement",
            "pr_body": "Body",
            "pr_labels": ["prompts"],
            "base_branch": "main",
            "head_sha": "abc123",
            "repo_full_name": "org/repo",
            "all_changed_files": [
                {"path": "prompts/citation.md", "change_kind": "modification"},
                {"path": "src/config.py", "change_kind": "modification"},
            ],
            "hint_matched_artifacts": [
                {
                    "path": "prompts/citation.md",
                    "artifact_class": "behavior_defining",
                    "artifact_type": "system_prompt",
                    "change_kind": "modification",
                    "before_content": "before",
                    "after_content": "after",
                    "raw_diff": "@@ -1 +1 @@",
                    "before_sha": "sha256:1",
                    "after_sha": "sha256:2",
                }
            ],
            "unchanged_hint_matches": [],
            "has_changes": False,
            "artifact_count": 0,
        }
    )

    assert model.has_changes is True
    assert model.artifact_count == 2  # counts all_changed_files


def test_raw_change_data_rejects_invalid_change_kind() -> None:
    with pytest.raises(ValidationError):
        RawChangeData.model_validate(
            {
                "pr_number": 1,
                "pr_title": "Title",
                "pr_body": "",
                "base_branch": "main",
                "head_sha": "abc",
                "repo_full_name": "org/repo",
                "all_changed_files": [
                    {"path": "prompts/x.md", "change_kind": "edited"},
                ],
                "hint_matched_artifacts": [],
                "unchanged_hint_matches": [],
                "has_changes": True,
                "artifact_count": 1,
            }
        )


def test_eval_case_normalizes_conversation_input() -> None:
    case = EvalCase.model_validate(
        {
            "id": "case_1",
            "source_platform": "promptfoo",
            "source_dataset_id": "dataset",
            "source_dataset_name": "dataset",
            "input_raw": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
            "expected_output": {"answer": "Hi"},
        }
    )

    assert case.is_conversational is True
    assert case.input_normalized == "USER: Hello\nASSISTANT: Hi"
    assert case.expected_output == "Hi"


def test_behavior_change_manifest_rejects_true_without_changes() -> None:
    with pytest.raises(ValidationError):
        BehaviorChangeManifest.model_validate(
            {
                "run_id": "run",
                "pr_number": 1,
                "commit_sha": "abc",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "has_changes": True,
                "overall_risk": "medium",
                "pr_intent_summary": "summary",
                "pr_description_alignment": "confirmed",
                "compound_change_detected": False,
                "changes": [],
                "compound_changes": [],
            }
        )


def test_coverage_gap_manifest_validates_similarity_bounds() -> None:
    with pytest.raises(ValidationError):
        CoverageGapManifest.model_validate(
            {
                "run_id": "run",
                "stage1_run_id": "stage1",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "unmapped_artifacts": [],
                "coverage_summary": {
                    "total_relevant_cases": 1,
                    "cases_covering_changed_behavior": 1,
                    "coverage_ratio": 0.5,
                    "platform": "langsmith",
                    "dataset": "dataset",
                },
                "gaps": [
                    {
                        "gap_id": "gap_1",
                        "artifact_path": "prompts/citation.md",
                        "gap_type": "uncovered",
                        "related_risk_flag": "flag",
                        "description": "desc",
                        "priority": "high",
                        "guardrail_direction": None,
                        "is_conversational": False,
                        "nearest_existing_cases": [
                            {
                                "case_id": "case_1",
                                "input_normalized": "question",
                                "similarity": 1.5,
                                "classification": "related",
                            }
                        ],
                    }
                ],
            }
        )


def test_coverage_summary_requires_reason_in_bootstrap_mode() -> None:
    with pytest.raises(ValidationError):
        CoverageGapManifest.model_validate(
            {
                "run_id": "run",
                "stage1_run_id": "stage1",
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "unmapped_artifacts": [],
                "coverage_summary": {
                    "total_relevant_cases": 0,
                    "cases_covering_changed_behavior": 0,
                    "coverage_ratio": 0.0,
                    "mode": "bootstrap",
                    "corpus_status": "empty",
                },
                "gaps": [],
            }
        )


def test_coverage_summary_accepts_bootstrap_mode_with_reason() -> None:
    manifest = CoverageGapManifest.model_validate(
        {
            "run_id": "run",
            "stage1_run_id": "stage1",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "unmapped_artifacts": [],
            "coverage_summary": {
                "total_relevant_cases": 0,
                "cases_covering_changed_behavior": 0,
                "coverage_ratio": 0.0,
                "mode": "bootstrap",
                "corpus_status": "empty",
                "bootstrap_reason": "No existing eval cases were found for the mapped artifact.",
            },
            "gaps": [],
        }
    )

    assert manifest.coverage_summary is not None
    assert manifest.coverage_summary.mode == "bootstrap"


def test_coverage_summary_accepts_coverage_aware_with_retrieval_notes() -> None:
    manifest = CoverageGapManifest.model_validate(
        {
            "run_id": "run",
            "stage1_run_id": "stage1",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "unmapped_artifacts": [],
            "coverage_summary": {
                "total_relevant_cases": 5,
                "cases_covering_changed_behavior": 0,
                "coverage_ratio": 0.0,
                "platform": "langsmith",
                "dataset": "dataset",
                "mode": "coverage_aware",
                "corpus_status": "available",
                "retrieval_notes": "Retrieved 5 cases via file-based fallback after MCP lookup was unavailable.",
            },
            "gaps": [],
        }
    )

    assert manifest.coverage_summary is not None
    assert manifest.coverage_summary.mode == "coverage_aware"
    assert manifest.coverage_summary.retrieval_notes is not None


def test_probe_proposal_recomputes_probe_count() -> None:
    proposal = ProbeProposal.model_validate(
        {
            "run_id": "run",
            "stage1_run_id": "stage1",
            "stage2_run_id": "stage2",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "pr_number": 1,
            "commit_sha": "abc",
            "probe_count": 999,
            "probes": [
                {
                    "probe_id": "probe_1",
                    "gap_id": "gap_1",
                    "probe_type": "boundary_probe",
                    "is_conversational": False,
                    "input": "Hello",
                    "input_format": "string",
                    "expected_behavior": "Expected",
                    "expected_behavior_type": "llm_rubric",
                    "rubric": "Rubric",
                    "probe_rationale": "Why",
                    "related_risk_flag": "flag",
                    "nearest_existing_case_id": "case_1",
                    "nearest_existing_similarity": 0.74,
                    "specificity_confidence": 0.9,
                    "testability_confidence": 0.8,
                    "realism_confidence": 0.7,
                    "approved": False,
                }
            ],
            "export_formats": {
                "promptfoo": "probes.yaml",
                "deepeval": None,
                "raw_json": "probes.json",
            },
        }
    )

    assert proposal.probe_count == 1
