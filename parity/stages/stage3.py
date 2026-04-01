from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parity.config import ParityConfig, ResolvedSpendCaps
from parity.context import count_tokens
from parity.errors import SchemaValidationError
from parity.models import (
    CoverageGap,
    EvalAnalysisManifest,
    EvalIntentCandidateBundle,
    EvalProposalManifest,
    ProbeIntent,
    ProbeIntentDraft,
    normalize_behavior_change_manifest_payload,
)
from parity.prompts.stage3_template import (
    compute_stage3_input_context_limit_tokens,
    render_stage3_prompt,
)
from parity.renderers import build_evaluator_plan, build_native_rendering
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema
from parity.stages.security import build_stage3_options
from parity.stages.stage3_mcp import build_stage3_mcp_server
from parity.tools.similarity import apply_intent_diversity_limit, rank_probe_intents


def _proposal_target_warnings(resolved_targets: list) -> list[str]:
    warnings: list[str] = []
    for target in resolved_targets:
        if target.profile.platform == "braintrust" and not (target.profile.project or "").strip():
            warnings.append(
                f"Target `{target.profile.target_id}` is missing Braintrust project metadata, so proposed evals for it are review-only and will not be auto-written."
            )
    return warnings


def _proposal_target_profile(resolved_target):
    profile = resolved_target.profile
    if profile.platform == "braintrust" and not (profile.project or "").strip() and profile.write_capability == "native_ready":
        return profile.model_copy(update={"write_capability": "review_only"})
    return profile


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _usable_conversation_payload(draft: ProbeIntentDraft) -> tuple[list[dict[str, str]], list[str]]:
    messages: list[dict[str, str]] = []
    dropped_indexes: list[int] = []
    for index, message in enumerate(draft.conversation_input, start=1):
        role = (message.role or "").strip()
        content = message.content
        if not role or not isinstance(content, str) or not content.strip():
            dropped_indexes.append(index)
            continue
        messages.append({"role": role, "content": content})

    warnings: list[str] = []
    if dropped_indexes:
        warnings.append(
            f"Intent `{draft.intent_id}` dropped malformed conversation turns at positions {dropped_indexes} during host assembly."
        )
    return messages, warnings


def _resolve_draft_input(
    draft: ProbeIntentDraft,
    *,
    conversational_gap: bool,
) -> tuple[str, str | dict | list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    conversation_payload, conversation_warnings = _usable_conversation_payload(draft)
    warnings.extend(conversation_warnings)

    candidates: dict[str, str | dict | list[dict[str, str]]] = {}
    if draft.string_input is not None:
        candidates["string"] = draft.string_input
    if draft.dict_input is not None:
        candidates["dict"] = draft.dict_input
    if conversation_payload:
        candidates["conversation"] = conversation_payload

    if draft.input_format in candidates:
        chosen_format = draft.input_format
        chosen_input = candidates[chosen_format]
    elif draft.input_format == "conversation" and draft.string_input is not None:
        chosen_format = "conversation"
        chosen_input = [{"role": "user", "content": draft.string_input}]
        warnings.append(
            f"Intent `{draft.intent_id}` declared `conversation` input but only provided `string_input`; "
            "host wrapped it into a single-turn conversation."
        )
    elif len(candidates) == 1:
        chosen_format, chosen_input = next(iter(candidates.items()))
        warnings.append(
            f"Intent `{draft.intent_id}` declared input_format `{draft.input_format}` but only "
            f"`{chosen_format}` input was usable; host corrected the format."
        )
    elif candidates:
        for fallback_format in ("conversation", "dict", "string"):
            if fallback_format in candidates:
                chosen_format = fallback_format
                chosen_input = candidates[fallback_format]
                break
        warnings.append(
            f"Intent `{draft.intent_id}` populated conflicting input fields; host kept `{chosen_format}` and ignored the rest."
        )
    else:
        raise ValueError("did not provide any usable input fields")

    if conversational_gap and chosen_format != "conversation":
        if chosen_format == "string":
            warnings.append(
                f"Intent `{draft.intent_id}` targeted a conversational gap without `conversation_input`; "
                "host wrapped `string_input` into a single-turn conversation."
            )
            chosen_format = "conversation"
            chosen_input = [{"role": "user", "content": str(chosen_input)}]
        else:
            raise ValueError("conversational gaps require `conversation_input` or a `string_input` fallback")

    return chosen_format, chosen_input, warnings


def _materialize_probe_intent_draft(
    draft: ProbeIntentDraft,
    gap: CoverageGap,
) -> tuple[ProbeIntent, list[str]]:
    input_format, resolved_input, warnings = _resolve_draft_input(
        draft,
        conversational_gap=gap.is_conversational,
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": draft.intent_id,
            "gap_id": gap.gap_id,
            "target_id": gap.target_id,
            "method_kind": gap.method_kind,
            "intent_type": draft.intent_type,
            "title": draft.title,
            "is_conversational": input_format == "conversation",
            "input": resolved_input,
            "input_format": input_format,
            "behavior_under_test": draft.behavior_under_test,
            "pass_criteria": draft.pass_criteria,
            "failure_mode": draft.failure_mode,
            "probe_rationale": draft.probe_rationale,
            "related_risk_flag": gap.related_risk_flag,
            "native_metadata_hints": (
                {"recommended_eval_area": gap.recommended_eval_area}
                if gap.recommended_eval_area
                else {}
            ),
            "native_tag_hints": _dedupe_strings([gap.recommended_eval_area or ""]),
            "native_shape_notes": list(gap.native_shape_hints),
            "evaluator_dossier_id": gap.evaluator_dossier_ids[0] if len(gap.evaluator_dossier_ids) == 1 else None,
            "nearest_existing_case_id": draft.nearest_existing_case_id,
            "nearest_existing_similarity": draft.nearest_existing_similarity,
            "specificity_confidence": draft.specificity_confidence,
            "testability_confidence": draft.testability_confidence,
            "novelty_confidence": draft.novelty_confidence,
            "realism_confidence": draft.realism_confidence,
            "target_fit_confidence": draft.target_fit_confidence,
        }
    )
    return intent, warnings


def materialize_intent_candidates(
    candidate_bundle: EvalIntentCandidateBundle,
    analysis: EvalAnalysisManifest,
) -> tuple[list[ProbeIntent], list[str]]:
    gap_lookup = {gap.gap_id: gap for gap in analysis.gaps}
    materialized: list[ProbeIntent] = []
    warnings: list[str] = []

    for draft in candidate_bundle.intents:
        gap = gap_lookup.get(draft.gap_id)
        if gap is None:
            warnings.append(f"Intent `{draft.intent_id}` referenced unknown gap `{draft.gap_id}` and was dropped.")
            continue
        try:
            intent, draft_warnings = _materialize_probe_intent_draft(draft, gap)
        except Exception as exc:
            warnings.append(f"Intent `{draft.intent_id}` was dropped during host assembly: {exc}")
            continue
        materialized.append(intent)
        warnings.extend(draft_warnings)

    return materialized, warnings


def run_stage3(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
    resolved_spend: ResolvedSpendCaps | None = None,
) -> StageRunResult:
    stage1_manifest = normalize_behavior_change_manifest_payload(stage1_manifest)
    run_id = f"stage3-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_spend = resolved_spend or config.resolve_spend_caps()
    candidate_intent_pool_limit = config.generation.resolve_candidate_intent_pool_limit()
    stage3_input_context_limit_tokens = compute_stage3_input_context_limit_tokens(candidate_intent_pool_limit)
    prompt = render_stage3_prompt(
        stage1_manifest,
        stage2_manifest,
        context,
        proposal_limit=config.generation.proposal_limit,
        candidate_intent_pool_limit=candidate_intent_pool_limit,
    )

    gap_count = len(stage2_manifest.get("gaps", []))
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-3] gaps_to_probe={gap_count} candidate_intent_pool_limit={candidate_intent_pool_limit} "
        f"proposal_limit={config.generation.proposal_limit} "
        f"input_context_limit_tokens={stage3_input_context_limit_tokens} prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(EvalIntentCandidateBundle.model_json_schema())
    repo_root = Path(cwd or Path.cwd()).resolve()
    stage3_runtime = build_stage3_mcp_server(analysis_manifest=stage2_manifest, repo_root=repo_root)
    options = build_stage3_options(
        cwd=str(repo_root),
        max_turns=25,
        max_budget_usd=resolved_spend.stage3_agent_cap_usd,
        output_schema=output_schema,
        mcp_servers={
            "parity_stage3": {
                "type": "sdk",
                "name": "parity-stage3",
                "instance": stage3_runtime.server._mcp_server,
            }
        },
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=3,
            prompt=prompt,
            options=options,
            output_model=EvalIntentCandidateBundle,
        )
    )

    analysis = EvalAnalysisManifest.model_validate(stage2_manifest)
    raw_intent_count = len(result.data.intents)
    materialized_intents, materialization_warnings = materialize_intent_candidates(result.data, analysis)
    dropped_intent_count = raw_intent_count - len(materialized_intents)
    print(
        f"[stage-3] intents_raw={raw_intent_count} intents_materialized={len(materialized_intents)} "
        f"intents_dropped={dropped_intent_count}",
        file=sys.stderr,
        flush=True,
    )

    if gap_count > 0 and raw_intent_count > 0 and not materialized_intents:
        preview = "; ".join(materialization_warnings[:3]) or "No usable probe intents survived host assembly."
        raise SchemaValidationError(
            f"Stage 3 produced no usable probe intents after host assembly. {preview}",
            stage=3,
            details={
                "dropped_intent_count": dropped_intent_count,
                "materialization_warnings": materialization_warnings,
            },
        )

    ranked = rank_probe_intents(materialized_intents, analysis.gaps)
    print(f"[stage-3] intents_ranked={len(ranked)}", file=sys.stderr, flush=True)

    diversified = apply_intent_diversity_limit(
        ranked,
        limit_per_gap=config.generation.diversity_limit_per_gap,
    )
    print(
        f"[stage-3] intents_after_diversity_limit={len(diversified)} "
        f"(limit_per_gap={config.generation.diversity_limit_per_gap})",
        file=sys.stderr,
        flush=True,
    )

    final_intents = diversified[: config.generation.proposal_limit]
    print(
        f"[stage-3] intents_final={len(final_intents)} "
        f"(proposal_limit={config.generation.proposal_limit})",
        file=sys.stderr,
        flush=True,
    )

    resolved_targets = {target.profile.target_id: target for target in analysis.resolved_targets}
    target_profiles = {
        target.profile.target_id: _proposal_target_profile(target)
        for target in analysis.resolved_targets
    }
    renderings = [
        build_native_rendering(
            intent,
            resolved_target=resolved_targets[intent.target_id],
            min_render_confidence=config.evals.write.min_render_confidence,
        )
        for intent in final_intents
        if intent.target_id in resolved_targets
    ]
    evaluator_plans = [
        build_evaluator_plan(
            intent,
            resolved_target=resolved_targets[intent.target_id],
            evaluator_config=config.evals.evaluators,
        )
        for intent in final_intents
        if intent.target_id in resolved_targets
    ]

    warnings = list(result.data.eval_quality_notes)
    warnings.extend(materialization_warnings)
    if context.warnings:
        warnings.extend(context.warnings)
    warnings.extend(_proposal_target_warnings(analysis.resolved_targets))
    warnings = list(dict.fromkeys(warnings))

    manifest = EvalProposalManifest(
        run_id=run_id,
        stage1_run_id=stage1_manifest.get("run_id", ""),
        stage2_run_id=analysis.run_id,
        stage3_run_id=run_id,
        timestamp=timestamp,
        pr_number=stage1_manifest.get("pr_number"),
        commit_sha=stage1_manifest.get("commit_sha", ""),
        intent_count=len(final_intents),
        targets=list(target_profiles.values()),
        intents=final_intents,
        evaluator_plans=evaluator_plans,
        renderings=renderings,
        render_artifacts=[],
        warnings=warnings,
    )
    result.data = manifest
    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
        "candidate_intent_pool_limit": candidate_intent_pool_limit,
        "proposal_limit": config.generation.proposal_limit,
        "stage3_input_context_limit_tokens": stage3_input_context_limit_tokens,
        "intents_raw": raw_intent_count,
        "intents_materialized": len(materialized_intents),
        "intents_dropped": dropped_intent_count,
        "intents_after_diversity_limit": len(diversified),
        "intents_final": len(final_intents),
        "resolved_spend_caps": {
            "analysis_total_spend_cap_usd": resolved_spend.analysis_total_spend_cap_usd,
            "stage1_agent_cap_usd": resolved_spend.stage1_agent_cap_usd,
            "stage2_agent_cap_usd": resolved_spend.stage2_agent_cap_usd,
            "stage2_embedding_cap_usd": resolved_spend.stage2_embedding_cap_usd,
            "stage3_agent_cap_usd": resolved_spend.stage3_agent_cap_usd,
            "source": resolved_spend.source,
        },
    }
    return result
