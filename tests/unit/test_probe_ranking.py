from __future__ import annotations

from datetime import datetime, timezone

from probegen.models.manifests import BehaviorChangeManifest, CoverageGap
from probegen.models.probes import ProbeCase
from probegen.tools.similarity import apply_diversity_limit, compute_probe_count, rank_probes


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


def test_compute_probe_count_respects_risk_multiplier_and_caps() -> None:
    manifest = BehaviorChangeManifest.model_validate(
        {
            "run_id": "run_1",
            "pr_number": 1,
            "commit_sha": "abc",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "has_changes": True,
            "overall_risk": "high",
            "pr_intent_summary": "summary",
            "pr_description_alignment": "confirmed",
            "compound_change_detected": True,
            "changes": [
                {
                    "artifact_path": f"prompts/{index}.md",
                    "artifact_type": "system_prompt",
                    "artifact_class": "behavior_defining",
                    "change_type": "modification",
                    "inferred_intent": "intent",
                    "pr_description_alignment": "confirmed",
                    "unintended_risk_flags": [],
                    "affected_components": [],
                    "false_negative_risks": [],
                    "false_positive_risks": [],
                    "change_summary": "summary",
                    "before_hash": "sha256:1",
                    "after_hash": "sha256:2",
                }
                for index in range(4)
            ],
            "compound_changes": [],
        }
    )

    assert compute_probe_count(manifest) == 12
