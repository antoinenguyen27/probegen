from __future__ import annotations

import json
from typing import Any

from parity.context import count_tokens, sample_traces, trim_collection_to_budget, truncate_text

STAGE3_MODEL_CONTEXT_WINDOW_TOKENS = 100_000
STAGE3_RESPONSE_HEADROOM_BASE_TOKENS = 14_000
STAGE3_RESPONSE_HEADROOM_PER_CANDIDATE_TOKENS = 450
STAGE3_MIN_INPUT_CONTEXT_LIMIT_TOKENS = 50_000

STAGE3_SYSTEM_TEMPLATE = """You are a native eval synthesis specialist for LLM-based agent systems.

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

STAGE 2 ANALYSIS SUMMARY:
{analysis_summary_json}

GAP SUMMARIES:
{gaps_json}

HOST-OWNED EVIDENCE TOOLS:
- `list_gap_dossiers`
- `read_gap_dossier`
- `list_targets`
- `read_target_profile`
- `list_evaluator_dossiers`
- `read_evaluator_dossier`
- `read_target_samples`
- `read_case_snapshot`
- `read_repo_eval_asset_excerpt`

GOAL:
Generate semantic probe intents that fit the discovered eval method and native row conventions for each target. Use the evidence tools to study native sample rows, repo-local eval assets, and evaluator dossiers before choosing the semantic input shape, behavior under test, pass criteria, and failure mode. The host will derive target wiring details from the selected gap and discovered target evidence.

QUALITY CRITERIA:
- Specific to the diff
- Testable in the discovered eval idiom
- Novel relative to compatible nearest cases
- Realistic for the product and users
- Strong fit for the resolved target and method
- Preserve native-feeling row attributes instead of reverting to generic eval shapes
- Generate up to {candidate_intent_pool_limit} candidate intents
- Return the full candidate pool in `intents`
- The host will rerank, diversify, and keep at most {proposal_limit} final intents for review

RULES:
- Respect the `gap_id` you choose. The host will derive `target_id`, `method_kind`, `related_risk_flag`, default evaluator selection, native bindings, tags, and assertion scaffolding from that gap and the resolved target evidence.
- Populate exactly one of `string_input`, `dict_input`, or `conversation_input`.
- Set `input_format` to match the populated input field.
- When `conversation_input` is used, emit an array of `{{role, content}}` messages.
- Use multi-turn conversation histories when the gap is conversational. Prefer `conversation_input` over `string_input` for conversational gaps.
- Only use `dict_input` when the native target clearly expects structured object inputs from the evidence.
- For bootstrap gaps, generate plausible starter eval intents without pretending there is existing corpus coverage.
- Make `pass_criteria` explicit enough for a deterministic renderer to map into native assertions.
- `failure_mode` should explain the likely miss or regression the eval should expose.
- Use `eval_quality_notes` only for observations about eval quality: testability limitations, evaluator regime recommendations, assertion operator guidance, or confidence rationale. Do not report tool errors, permission issues, or infrastructure status — the host tracks those independently.

Output EvalIntentCandidateBundle JSON only. No prose.
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
                "change_summary": change.get("change_summary"),
                "unintended_risk_flags": change.get("unintended_risk_flags", []),
                "affected_components": change.get("affected_components", []),
                "behavioral_signatures": change.get("behavioral_signatures", []),
                "eval_search_hints": change.get("eval_search_hints", []),
                "validation_focus": change.get("validation_focus", []),
            }
            for change in stage1_manifest.get("changes", [])
        ],
    }


def compute_stage3_input_context_limit_tokens(candidate_intent_pool_limit: int) -> int:
    reserved_response_tokens = (
        STAGE3_RESPONSE_HEADROOM_BASE_TOKENS
        + (candidate_intent_pool_limit * STAGE3_RESPONSE_HEADROOM_PER_CANDIDATE_TOKENS)
    )
    return max(
        STAGE3_MIN_INPUT_CONTEXT_LIMIT_TOKENS,
        STAGE3_MODEL_CONTEXT_WINDOW_TOKENS - reserved_response_tokens,
    )


def render_stage3_prompt(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    *,
    proposal_limit: int,
    candidate_intent_pool_limit: int,
) -> str:
    stage1_brief = extract_stage1_brief(stage1_manifest)
    traces = sample_traces(context.traces_dir, max_samples=context.trace_max_samples)
    trimmed_traces = trim_collection_to_budget(traces, per_item_budget=300, total_budget=6000)
    trace_samples = "\n\n---\n\n".join(trimmed_traces)
    context_pack_limit_tokens = compute_stage3_input_context_limit_tokens(candidate_intent_pool_limit)
    analysis_summary = {
        "analysis_status": stage2_manifest.get("analysis_status", "complete"),
        "degradation_reason": stage2_manifest.get("degradation_reason"),
        "resolved_targets": [
            {
                "target_id": target.get("profile", {}).get("target_id"),
                "platform": target.get("profile", {}).get("platform"),
                "locator": target.get("profile", {}).get("locator"),
                "artifact_paths": target.get("profile", {}).get("artifact_paths", []),
                "method_kind": target.get("method_profile", {}).get("method_kind"),
                "renderability_status": target.get("method_profile", {}).get("renderability_status"),
                "supports_multi_assert": target.get("method_profile", {}).get("supports_multi_assert"),
                "evaluator_scope": target.get("method_profile", {}).get("evaluator_scope"),
                "execution_surface": target.get("method_profile", {}).get("execution_surface"),
                "formal_discovery_status": target.get("method_profile", {}).get("formal_discovery_status"),
                "formal_binding_count": target.get("method_profile", {}).get("formal_binding_count"),
                "binding_candidates": target.get("method_profile", {}).get("binding_candidates", []),
                "evaluator_dossier_count": len(target.get("evaluator_dossiers", [])),
                "resolution_notes": target.get("resolution_notes", []),
            }
            for target in stage2_manifest.get("resolved_targets", [])
        ],
        "coverage_by_target": stage2_manifest.get("coverage_by_target", []),
    }
    gap_summaries = [
        {
            "gap_id": gap.get("gap_id"),
            "artifact_path": gap.get("artifact_path"),
            "target_id": gap.get("target_id"),
            "method_kind": gap.get("method_kind"),
            "gap_type": gap.get("gap_type"),
            "related_risk_flag": gap.get("related_risk_flag"),
            "description": gap.get("description"),
            "why_gap_is_real": gap.get("why_gap_is_real"),
            "recommended_eval_area": gap.get("recommended_eval_area"),
            "recommended_eval_mode": gap.get("recommended_eval_mode"),
            "evaluator_dossier_ids": gap.get("evaluator_dossier_ids", []),
            "native_shape_hints": gap.get("native_shape_hints", []),
            "profile_status": gap.get("profile_status"),
            "is_conversational": gap.get("is_conversational", False),
            "confidence": gap.get("confidence"),
        }
        for gap in stage2_manifest.get("gaps", [])
    ]

    rendered = STAGE3_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 4000),
        users_context=truncate_text(context.users, 2000),
        interactions_context=truncate_text(context.interactions, 3000),
        good_examples=truncate_text(context.good_examples, 3000),
        bad_examples=truncate_text(context.bad_examples, 4000),
        trace_samples=trace_samples,
        stage1_brief_json=json.dumps(stage1_brief, indent=2),
        analysis_summary_json=json.dumps(analysis_summary, indent=2),
        gaps_json=json.dumps(gap_summaries, indent=2),
        proposal_limit=proposal_limit,
        candidate_intent_pool_limit=candidate_intent_pool_limit,
    )

    if count_tokens(rendered) <= context_pack_limit_tokens:
        return rendered

    return STAGE3_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 3000),
        users_context=truncate_text(context.users, 1500),
        interactions_context=truncate_text(context.interactions, 2000),
        good_examples=truncate_text(context.good_examples, 1500),
        bad_examples=truncate_text(context.bad_examples, 2000),
        trace_samples="",
        stage1_brief_json=json.dumps(stage1_brief, indent=2),
        analysis_summary_json=json.dumps(analysis_summary, indent=2),
        gaps_json=json.dumps(gap_summaries, indent=2),
        proposal_limit=proposal_limit,
        candidate_intent_pool_limit=candidate_intent_pool_limit,
    )
