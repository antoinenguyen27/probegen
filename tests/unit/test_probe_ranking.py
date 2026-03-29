from __future__ import annotations

from parity.models.manifests import CoverageGap
from parity.models.probes import ProbeCase
from parity.tools.similarity import apply_diversity_limit, rank_probes


def _probe(probe_id: str, gap_id: str, specificity: float, testability: float, realism: float, similarity: float, *, probe_type: str = "boundary_probe") -> ProbeCase:
    return ProbeCase.model_validate(
        {
            "probe_id": probe_id,
            "gap_id": gap_id,
            "probe_type": probe_type,
            "is_conversational": False,
            "input": "input",
            "input_format": "string",
            "expected_behavior": "behavior",
            "expected_behavior_type": "llm_rubric",
            "rubric": "rubric",
            "probe_rationale": "why",
            "related_risk_flag": "flag",
            "nearest_existing_case_id": "case_1",
            "nearest_existing_similarity": similarity,
            "specificity_confidence": specificity,
            "testability_confidence": testability,
            "realism_confidence": realism,
            "approved": False,
        }
    )


def test_rank_probes_prefers_higher_composite_score() -> None:
    gaps = [
        CoverageGap.model_validate(
            {
                "gap_id": "gap_high",
                "artifact_path": "prompts/a.md",
                "gap_type": "uncovered",
                "related_risk_flag": "flag",
                "description": "desc",
                "nearest_existing_cases": [],
                "priority": "high",
                "guardrail_direction": None,
                "is_conversational": False,
            }
        )
    ]
    probes = [
        _probe("probe_low", "gap_high", 0.6, 0.6, 0.6, 0.8),
        _probe("probe_high", "gap_high", 0.9, 0.9, 0.9, 0.2),
    ]

    ranked = rank_probes(probes, gaps)

    assert ranked[0].probe_id == "probe_high"


def test_apply_diversity_limit_caps_probes_per_gap() -> None:
    probes = [
        _probe("p1", "gap1", 0.9, 0.9, 0.9, 0.1),
        _probe("p2", "gap1", 0.8, 0.8, 0.8, 0.1),
        _probe("p3", "gap1", 0.7, 0.7, 0.7, 0.1),
        _probe("p4", "gap2", 0.9, 0.9, 0.9, 0.1),
    ]

    limited = apply_diversity_limit(probes, limit_per_gap=2)

    assert [probe.probe_id for probe in limited] == ["p1", "p2", "p4"]
