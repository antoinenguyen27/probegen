from __future__ import annotations

from parity.stages.stage2 import _normalize_stage2_payload


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
