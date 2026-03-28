from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import numpy as np

from parity.models.manifests import CoverageGap
from parity.models.probes import ProbeCase


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_vector = np.asarray(list(left), dtype=float)
    right_vector = np.asarray(list(right), dtype=float)
    if left_vector.size == 0 or right_vector.size == 0:
        return 0.0
    left_norm = np.linalg.norm(left_vector)
    right_norm = np.linalg.norm(right_vector)
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(np.dot(left_vector, right_vector) / (left_norm * right_norm))


def classify_similarity(
    score: float,
    *,
    duplicate_threshold: float,
    boundary_threshold: float,
) -> str:
    if score >= duplicate_threshold:
        return "duplicate"
    if score >= boundary_threshold:
        return "boundary"
    if score >= 0.50:
        return "related"
    return "novel"


def compute_probe_count(manifest) -> int:
    base = len(manifest.changes) * 3
    risk_multiplier = {"low": 0.6, "medium": 1.0, "high": 1.4}[manifest.overall_risk]
    if manifest.compound_change_detected:
        risk_multiplier *= 1.3
    raw = int(base * risk_multiplier)
    return max(3, min(raw, 12))


def score_probe(probe: ProbeCase, gaps: list[CoverageGap]) -> float:
    weights = {
        "specificity": 0.30,
        "testability": 0.25,
        "novelty": 0.20,
        "realism": 0.15,
        "risk_alignment": 0.10,
    }
    novelty = 1.0 - (probe.nearest_existing_similarity or 0.0)
    gap = next((candidate for candidate in gaps if candidate.gap_id == probe.gap_id), None)
    risk_alignment = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
        gap.priority if gap else "medium",
        0.6,
    )
    return (
        weights["specificity"] * probe.specificity_confidence
        + weights["testability"] * probe.testability_confidence
        + weights["novelty"] * novelty
        + weights["realism"] * probe.realism_confidence
        + weights["risk_alignment"] * risk_alignment
    )


def apply_diversity_limit(probes: list[ProbeCase], *, limit_per_gap: int) -> list[ProbeCase]:
    counts: dict[str, int] = defaultdict(int)
    filtered: list[ProbeCase] = []
    for probe in probes:
        if counts[probe.gap_id] >= limit_per_gap:
            continue
        filtered.append(probe)
        counts[probe.gap_id] += 1
    return filtered


def rank_probes(probes: list[ProbeCase], gaps: list[CoverageGap]) -> list[ProbeCase]:
    return sorted(probes, key=lambda probe: score_probe(probe, gaps), reverse=True)
