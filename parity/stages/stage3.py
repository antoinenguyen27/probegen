from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.models import CoverageGap, ProbeProposal
from parity.prompts.stage3_template import (
    compute_stage3_input_context_limit_tokens,
    render_stage3_prompt,
)
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema
from parity.stages.security import build_stage3_options
from parity.tools.similarity import apply_diversity_limit, rank_probes

_STAGE3_INJECT_KEYS = {
    "run_id", "stage1_run_id", "stage2_run_id", "timestamp",
    "pr_number", "commit_sha", "probe_count", "schema_version",
    "export_formats", "warnings",
}


def run_stage3(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    run_id = f"stage3-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_spend = config.resolve_spend_caps()
    candidate_probe_pool_limit = config.generation.resolve_candidate_probe_pool_limit()
    stage3_input_context_limit_tokens = compute_stage3_input_context_limit_tokens(candidate_probe_pool_limit)
    prompt = render_stage3_prompt(
        stage1_manifest,
        stage2_manifest,
        context,
        proposal_probe_limit=config.generation.proposal_probe_limit,
        candidate_probe_pool_limit=candidate_probe_pool_limit,
    )

    gap_count = len(stage2_manifest.get("gaps", []))
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-3] gaps_to_probe={gap_count} candidate_probe_pool_limit={candidate_probe_pool_limit} "
        f"proposal_probe_limit={config.generation.proposal_probe_limit} "
        f"input_context_limit_tokens={stage3_input_context_limit_tokens} prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        ProbeProposal.model_json_schema(),
        remove_keys=_STAGE3_INJECT_KEYS,
    )

    options = build_stage3_options(
        cwd=str(cwd or Path.cwd()),
        max_turns=25,
        max_budget_usd=resolved_spend.stage3_agent_cap_usd,
        output_schema=output_schema,
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=3,
            prompt=prompt,
            options=options,
            output_model=ProbeProposal,
            inject_fields={
                "run_id": run_id,
                "stage1_run_id": stage1_manifest.get("run_id", ""),
                "stage2_run_id": stage2_manifest.get("run_id", ""),
                "timestamp": timestamp,
                "pr_number": stage1_manifest.get("pr_number"),
                "commit_sha": stage1_manifest.get("commit_sha", ""),
                "probe_count": 0,
            },
        )
    )

    gap_models = [CoverageGap.model_validate(gap) for gap in stage2_manifest.get("gaps", [])]
    raw_probe_count = len(result.data.probes)
    ranked = rank_probes(result.data.probes, gap_models)
    print(f"[stage-3] probes_raw={raw_probe_count} probes_ranked={len(ranked)}", file=sys.stderr, flush=True)

    diversified = apply_diversity_limit(
        ranked,
        limit_per_gap=config.generation.diversity_limit_per_gap,
    )
    print(
        f"[stage-3] probes_after_diversity_limit={len(diversified)} "
        f"(limit_per_gap={config.generation.diversity_limit_per_gap})",
        file=sys.stderr,
        flush=True,
    )

    result.data.probes = diversified[: config.generation.proposal_probe_limit]
    result.data.probe_count = len(result.data.probes)
    print(
        f"[stage-3] probes_final={result.data.probe_count} "
        f"(proposal_probe_limit={config.generation.proposal_probe_limit})",
        file=sys.stderr,
        flush=True,
    )

    if context.warnings:
        result.data.warnings.extend(context.warnings)
    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
        "candidate_probe_pool_limit": candidate_probe_pool_limit,
        "proposal_probe_limit": config.generation.proposal_probe_limit,
        "stage3_input_context_limit_tokens": stage3_input_context_limit_tokens,
        "probes_raw": raw_probe_count,
        "probes_after_diversity_limit": len(diversified),
        "probes_final": result.data.probe_count,
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
