from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parity.config import ParityConfig, ResolvedSpendCaps
from parity.context import count_tokens
from parity.models import EvalAnalysisManifest, EvalIntentCandidateBundle, EvalProposalManifest
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


def run_stage3(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
    resolved_spend: ResolvedSpendCaps | None = None,
) -> StageRunResult:
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
    ranked = rank_probe_intents(result.data.intents, analysis.gaps)
    print(f"[stage-3] intents_raw={raw_intent_count} intents_ranked={len(ranked)}", file=sys.stderr, flush=True)

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

    warnings = list(result.data.warnings)
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
