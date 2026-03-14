from __future__ import annotations

from pathlib import Path

from probegen.context import ContextPack
from probegen.prompts.stage2_template import render_stage2_prompt
from probegen.prompts.stage3_template import render_stage3_prompt


def test_stage2_prompt_includes_bootstrap_instructions() -> None:
    prompt = render_stage2_prompt(
        {
            "run_id": "stage1-run",
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "inferred_intent": "Require citations for factual answers",
                }
            ],
        }
    )

    assert "switch to bootstrap mode" in prompt
    assert "leave `nearest_existing_cases` empty" in prompt


def test_stage3_prompt_describes_bootstrap_mode() -> None:
    context = ContextPack(
        product="Acme assistant for support questions.",
        users="Support agents and end users.",
        interactions="Users ask factual and conversational follow-ups.",
        good_examples="Clear grounded answers.",
        bad_examples="Decorative citations on casual replies.",
        traces_dir=Path("/nonexistent"),
        trace_max_samples=5,
    )
    prompt = render_stage3_prompt(
        {
            "run_id": "stage1-run",
            "overall_risk": "high",
            "compound_change_detected": False,
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "inferred_intent": "Require citations for factual answers",
                    "unintended_risk_flags": ["Casual questions may receive citations unexpectedly"],
                    "affected_components": ["app/graph.py"],
                }
            ],
        },
        {
            "coverage_summary": {
                "total_relevant_cases": 0,
                "cases_covering_changed_behavior": 0,
                "coverage_ratio": 0.0,
                "mode": "bootstrap",
                "corpus_status": "empty",
                "bootstrap_reason": "No existing eval cases were found for this agent.",
            },
            "gaps": [
                {
                    "gap_id": "gap_001",
                    "artifact_path": "prompts/answer.md",
                    "gap_type": "uncovered",
                    "related_risk_flag": "Casual questions may receive citations unexpectedly",
                    "description": "No baseline coverage exists yet.",
                    "nearest_existing_cases": [],
                    "priority": "high",
                    "guardrail_direction": None,
                    "is_conversational": False,
                }
            ],
        },
        context,
        max_probes_surfaced=4,
    )

    assert "COVERAGE SUMMARY" in prompt
    assert '"mode": "bootstrap"' in prompt
    assert "there is no usable eval corpus for comparison" in prompt

