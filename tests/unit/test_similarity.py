from __future__ import annotations

from datetime import datetime, timezone

from probegen.models.manifests import CoverageGap
from probegen.models.probes import ProbeCase
from probegen.tools.similarity import classify_similarity, cosine_similarity


def test_cosine_similarity_basic_cases() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert round(cosine_similarity([1, 0], [0, 1]), 6) == 0.0
    assert cosine_similarity([0, 0], [1, 0]) == 0.0


def test_classify_similarity_uses_thresholds() -> None:
    assert classify_similarity(0.9, duplicate_threshold=0.88, boundary_threshold=0.72) == "duplicate"
    assert classify_similarity(0.8, duplicate_threshold=0.88, boundary_threshold=0.72) == "boundary"
    assert classify_similarity(0.6, duplicate_threshold=0.88, boundary_threshold=0.72) == "related"
    assert classify_similarity(0.4, duplicate_threshold=0.88, boundary_threshold=0.72) == "novel"
