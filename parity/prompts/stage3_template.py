from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from parity.context import count_tokens, sample_traces, trim_collection_to_budget, truncate_text

STAGE3_SYSTEM_TEMPLATE = """You are a behavioral probe generator for LLM-based agent systems.

PRODUCT CONTEXT:
{product_context}

USER PROFILES:
{users_context}

INTERACTION PATTERNS:
{interactions_context}

WHAT GOOD LOOKS LIKE:
{good_examples}

KNOWN FAILURE MODES:
{bad_examples}

REAL USER INTERACTION SAMPLES:
{trace_samples}

STAGE 1 BRIEF:
{stage1_brief_json}

COVERAGE SUMMARY:
{coverage_summary_json}

GAPS:
{gaps_json}

NEAREST EXISTING CASES:
{nearest_cases_json}

QUALITY CRITERIA:
- Specific to the diff
- Testable
- Novel relative to nearest existing cases
- Realistic for the product and users
- No more than {max_probes_surfaced} surfaced probes

BOOTSTRAP MODE:
If coverage_summary.mode is `bootstrap`, there is no usable eval corpus for comparison.
In that case:
- Generate plausible starter evals from the diff, system prompt, guardrails, product context,
  user profiles, interaction patterns, known failures, and trace samples.
- Treat empty nearest_existing_cases as intentional.
- Do not invent comparisons to missing evals.
- Prefer expected_improvement, regression_guard, overcorrection_probe, ambiguity_probe, and
  edge_case probes over boundary_probe unless a real nearest case exists.

{multi_turn_block}

Output ProbeProposal JSON only. No prose.
"""

MULTI_TURN_BLOCK = """MULTI-TURN PROBE GENERATION:
This gap is for a conversational agent. Generate probe inputs as full conversation
histories, not single-turn strings.

Rules:
- The conversation must have at least 2 turns before the test stimulus
- Prior turns must be realistic for this agent and these users
- The final user message is the test stimulus
- The expected behavior and rubric must target the final assistant response only
"""


def extract_stage1_brief(stage1_manifest: dict) -> dict:
    return {
        "run_id": stage1_manifest.get("run_id"),
        "overall_risk": stage1_manifest.get("overall_risk"),
        "compound_change_detected": stage1_manifest.get("compound_change_detected"),
        "changes": [
            {
                "artifact_path": change.get("artifact_path"),
                "inferred_intent": change.get("inferred_intent"),
                "unintended_risk_flags": change.get("unintended_risk_flags", []),
                "affected_components": change.get("affected_components", []),
            }
            for change in stage1_manifest.get("changes", [])
        ],
    }


def format_nearest_cases(gaps: list[dict[str, Any]], *, max_per_gap: int = 5) -> str:
    reduced: list[dict[str, Any]] = []
    for gap in gaps:
        cases = []
        for case in gap.get("nearest_existing_cases", [])[:max_per_gap]:
            reduced_case = dict(case)
            reduced_case["input_normalized"] = truncate_text(
                reduced_case.get("input_normalized", ""),
                200,
            )
            cases.append(reduced_case)
        reduced.append({"gap_id": gap.get("gap_id"), "nearest_existing_cases": cases})
    serialized = json.dumps(reduced, indent=2)
    return truncate_text(serialized, 4000)


def render_stage3_prompt(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    *,
    max_probes_surfaced: int,
) -> str:
    stage1_brief = extract_stage1_brief(stage1_manifest)
    traces = sample_traces(context.traces_dir, max_samples=context.trace_max_samples)
    trimmed_traces = trim_collection_to_budget(traces, per_item_budget=300, total_budget=6000)
    trace_samples = "\n\n---\n\n".join(trimmed_traces)
    multi_turn_block = (
        MULTI_TURN_BLOCK if any(gap.get("is_conversational") for gap in stage2_manifest.get("gaps", [])) else ""
    )

    rendered = STAGE3_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 4000),
        users_context=truncate_text(context.users, 2000),
        interactions_context=truncate_text(context.interactions, 3000),
        good_examples=truncate_text(context.good_examples, 3000),
        bad_examples=truncate_text(context.bad_examples, 4000),
        trace_samples=trace_samples,
        stage1_brief_json=json.dumps(stage1_brief, indent=2),
        coverage_summary_json=json.dumps(stage2_manifest.get("coverage_summary") or {}, indent=2),
        gaps_json=json.dumps(stage2_manifest.get("gaps", []), indent=2),
        nearest_cases_json=format_nearest_cases(stage2_manifest.get("gaps", []), max_per_gap=5),
        max_probes_surfaced=max_probes_surfaced,
        multi_turn_block=multi_turn_block,
    )

    if count_tokens(rendered) <= 80000:
        return rendered

    reduced_good = truncate_text(context.good_examples, 1500)
    reduced_bad = truncate_text(context.bad_examples, 2000)
    return STAGE3_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 4000),
        users_context=truncate_text(context.users, 2000),
        interactions_context=truncate_text(context.interactions, 3000),
        good_examples=reduced_good,
        bad_examples=reduced_bad,
        trace_samples="",
        stage1_brief_json=json.dumps(stage1_brief, indent=2),
        coverage_summary_json=json.dumps(stage2_manifest.get("coverage_summary") or {}, indent=2),
        gaps_json=json.dumps(stage2_manifest.get("gaps", []), indent=2),
        nearest_cases_json=format_nearest_cases(stage2_manifest.get("gaps", []), max_per_gap=5),
        max_probes_surfaced=max_probes_surfaced,
        multi_turn_block=multi_turn_block,
    )
