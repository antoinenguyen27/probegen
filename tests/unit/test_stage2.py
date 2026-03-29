from __future__ import annotations

from parity.config import ParityConfig
from parity.stages._common import format_tool_summary
from parity.stages.stage2 import (
    _build_stage2_bootstrap_brief,
    _build_stage2_budget_fallback,
    _build_stage2_mapping_resolutions,
    _normalize_stage2_payload,
)


def test_normalize_stage2_payload_moves_bootstrap_reason_to_retrieval_notes_for_coverage_aware() -> None:
    payload = {
        "coverage_summary": {
            "total_relevant_cases": 5,
            "cases_covering_changed_behavior": 0,
            "coverage_ratio": 0.0,
            "platform": "langsmith",
            "dataset": "lilian-weng-rag-baseline",
            "mode": "coverage_aware",
            "corpus_status": "available",
            "bootstrap_reason": "Corpus available via file-based fallback after MCP lookup failed.",
        },
        "gaps": [],
    }

    normalized = _normalize_stage2_payload(payload)

    assert normalized["coverage_summary"]["retrieval_notes"] == (
        "Corpus available via file-based fallback after MCP lookup failed."
    )
    assert "bootstrap_reason" not in normalized["coverage_summary"]


def test_normalize_stage2_payload_preserves_existing_retrieval_notes() -> None:
    payload = {
        "coverage_summary": {
            "mode": "coverage_aware",
            "corpus_status": "available",
            "retrieval_notes": "Retrieved real evals via file fallback.",
            "bootstrap_reason": "This note should not overwrite retrieval_notes.",
        }
    }

    normalized = _normalize_stage2_payload(payload)

    assert normalized["coverage_summary"]["retrieval_notes"] == "Retrieved real evals via file fallback."
    assert "bootstrap_reason" not in normalized["coverage_summary"]


def test_build_stage2_mapping_resolutions_uses_explicit_mapping() -> None:
    config = ParityConfig.model_validate(
        {
            "mappings": [
                {
                    "artifact": "prompts/**",
                    "platform": "langsmith",
                    "dataset": "citation-agent-evals",
                }
            ]
        }
    )

    resolutions = _build_stage2_mapping_resolutions(
        {
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "artifact_class": "system_prompt",
                }
            ]
        },
        config,
    )

    assert resolutions == [
        {
            "artifact_path": "prompts/answer.md",
            "artifact_class": "system_prompt",
            "mapping_status": "explicit",
            "resolution_source": "parity_yaml",
            "platform": "langsmith",
            "target": "citation-agent-evals",
            "project": None,
            "eval_type": None,
            "access_mode": "mcp",
        }
    ]


def test_build_stage2_mapping_resolutions_uses_promptfoo_path_when_dataset_missing() -> None:
    config = ParityConfig.model_validate(
        {
            "platforms": {
                "promptfoo": {
                    "config_path": "evals/promptfooconfig.yaml",
                }
            },
            "mappings": [
                {
                    "artifact": "prompts/**",
                    "platform": "promptfoo",
                }
            ],
        }
    )

    resolutions = _build_stage2_mapping_resolutions(
        {"changes": [{"artifact_path": "prompts/answer.md"}]},
        config,
    )

    assert resolutions[0]["mapping_status"] == "explicit"
    assert resolutions[0]["platform"] == "promptfoo"
    assert resolutions[0]["target"] == "evals/promptfooconfig.yaml"
    assert resolutions[0]["access_mode"] == "file"


def test_build_stage2_mapping_resolutions_marks_unmapped_artifact_unresolved() -> None:
    config = ParityConfig()

    resolutions = _build_stage2_mapping_resolutions(
        {"changes": [{"artifact_path": "prompts/answer.md"}]},
        config,
    )

    assert resolutions[0]["mapping_status"] == "unresolved"
    assert resolutions[0]["resolution_source"] == "none"
    assert resolutions[0]["target"] is None


def test_build_stage2_bootstrap_brief_dedupes_risk_flags() -> None:
    brief = _build_stage2_bootstrap_brief(
        {
            "overall_risk": "high",
            "compound_change_detected": True,
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "artifact_class": "system_prompt",
                    "inferred_intent": "Require citations",
                    "affected_components": ["app/graph.py"],
                    "unintended_risk_flags": ["Casual replies may gain citations", "Casual replies may gain citations"],
                    "false_negative_risks": ["Missing citations on factual claims"],
                    "false_positive_risks": ["Casual replies may gain citations"],
                }
            ],
        }
    )

    assert brief == {
        "overall_risk": "high",
        "compound_change_detected": True,
        "changes": [
            {
                "artifact_path": "prompts/answer.md",
                "artifact_class": "system_prompt",
                "inferred_intent": "Require citations",
                "affected_components": ["app/graph.py"],
                "risk_flags": [
                    "Casual replies may gain citations",
                    "Missing citations on factual claims",
                ],
            }
        ],
    }


def test_format_tool_summary_includes_counts_and_durations() -> None:
    summary = format_tool_summary(
        {"Read": 2, "Bash": 1},
        {"Read": 150, "Bash": 25},
    )

    assert summary == "Bash x1 (~25ms), Read x2 (~150ms)"


def test_stage2_budget_fallback_uses_retrieval_state_when_cases_exist() -> None:
    manifest = _build_stage2_budget_fallback(
        stage1_manifest={
            "run_id": "stage1-run",
            "overall_risk": "high",
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "inferred_intent": "Require citations",
                    "unintended_risk_flags": ["Casual replies may gain citations"],
                    "false_negative_risks": [],
                    "false_positive_risks": [],
                }
            ],
        },
        mapping_resolutions=[
            {
                "artifact_path": "prompts/answer.md",
                "mapping_status": "explicit",
            }
        ],
        run_id="stage2-run",
        timestamp="2026-03-30T00:00:00Z",
        runtime_metadata={
            "retrieval": {
                "fetch_request_count": 1,
                "total_cases": 5,
                "sources": [{"platform": "langsmith", "target": "citation-agent-evals"}],
            },
            "embedding": {
                "blocked_request_count": 1,
            },
        },
        reason="Agent spend cap was exhausted.",
    )

    assert manifest.coverage_summary is not None
    assert manifest.coverage_summary.mode == "coverage_aware"
    assert manifest.coverage_summary.total_relevant_cases == 5
    assert "Embedding spend cap blocked 1 embedding request(s)." in (manifest.coverage_summary.retrieval_notes or "")
    assert manifest.gaps[0].gap_type == "uncovered"


def test_stage2_budget_fallback_bootstraps_when_no_cases_exist() -> None:
    manifest = _build_stage2_budget_fallback(
        stage1_manifest={
            "run_id": "stage1-run",
            "overall_risk": "medium",
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "change_summary": "Require citations",
                    "unintended_risk_flags": [],
                    "false_negative_risks": [],
                    "false_positive_risks": [],
                }
            ],
        },
        mapping_resolutions=[
            {
                "artifact_path": "prompts/answer.md",
                "mapping_status": "unresolved",
            }
        ],
        run_id="stage2-run",
        timestamp="2026-03-30T00:00:00Z",
        runtime_metadata={
            "retrieval": {
                "fetch_request_count": 0,
                "total_cases": 0,
                "sources": [],
            },
            "embedding": {
                "blocked_request_count": 0,
            },
        },
        reason="Agent spend cap was exhausted.",
    )

    assert manifest.coverage_summary is not None
    assert manifest.coverage_summary.mode == "bootstrap"
    assert manifest.coverage_summary.corpus_status == "unavailable"
    assert "Unmapped artifacts: prompts/answer.md." in (manifest.coverage_summary.bootstrap_reason or "")
