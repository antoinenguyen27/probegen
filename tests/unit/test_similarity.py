from __future__ import annotations

from datetime import datetime, timezone

from parity.models.manifests import CoverageGap
from parity.models.probes import ProbeCase
from parity.tools.similarity import (
    classify_embedding_against_corpus,
    classify_embeddings_against_corpus,
    classify_similarity,
    cosine_similarity,
)


def test_cosine_similarity_basic_cases() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert round(cosine_similarity([1, 0], [0, 1]), 6) == 0.0
    assert cosine_similarity([0, 0], [1, 0]) == 0.0


def test_classify_similarity_uses_thresholds() -> None:
    assert classify_similarity(0.9, duplicate_threshold=0.88, boundary_threshold=0.72) == "duplicate"
    assert classify_similarity(0.8, duplicate_threshold=0.88, boundary_threshold=0.72) == "boundary"
    assert classify_similarity(0.6, duplicate_threshold=0.88, boundary_threshold=0.72) == "related"
    assert classify_similarity(0.4, duplicate_threshold=0.88, boundary_threshold=0.72) == "novel"


def test_classify_embedding_against_corpus_returns_ranked_payload() -> None:
    payload = classify_embedding_against_corpus(
        [1.0, 0.0],
        [
            {"id": "case_b", "embedding": [0.0, 1.0]},
            {"id": "case_a", "embedding": [1.0, 0.0]},
        ],
        candidate_id="risk_001",
        duplicate_threshold=0.88,
        boundary_threshold=0.72,
    )

    assert payload["candidate_id"] == "risk_001"
    assert payload["top_match"]["corpus_id"] == "case_a"
    assert payload["overall_classification"] == "duplicate"
    assert payload["results"][0]["corpus_id"] == "case_a"


def test_classify_embeddings_against_corpus_preserves_per_candidate_results() -> None:
    payloads = classify_embeddings_against_corpus(
        [
            {"id": "risk_001", "embedding": [1.0, 0.0]},
            {"id": "risk_002", "embedding": [0.0, 1.0]},
        ],
        [
            {"id": "case_a", "embedding": [1.0, 0.0]},
            {"id": "case_b", "embedding": [0.0, 1.0]},
        ],
        duplicate_threshold=0.88,
        boundary_threshold=0.72,
    )

    assert len(payloads) == 2
    assert payloads[0]["candidate_id"] == "risk_001"
    assert payloads[0]["top_match"]["corpus_id"] == "case_a"
    assert payloads[1]["candidate_id"] == "risk_002"
    assert payloads[1]["top_match"]["corpus_id"] == "case_b"
