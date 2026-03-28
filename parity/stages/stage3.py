from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.models import CoverageGap, ProbeProposal
from parity.prompts.stage3_template import render_stage3_prompt
from parity.stages._common import StageRunResult, build_metadata, run_stage_with_retry, simplify_schema
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
    prompt = render_stage3_prompt(
        stage1_manifest,
        stage2_manifest,
        context,
        max_probes_surfaced=config.generation.max_probes_surfaced,
    )

    gap_count = len(stage2_manifest.get("gaps", []))
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-3] gaps_to_probe={gap_count} max_probes_surfaced={config.generation.max_probes_surfaced} "
        f"prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        ProbeProposal.model_json_schema(),
        remove_keys=_STAGE3_INJECT_KEYS,
    )

    options = ClaudeAgentOptions(
        allowed_tools=[],  # Stage 3 is pure generation from prompt context.
                           # Ranking and diversity filtering happen post-generation in the orchestrator (similarity.py).
        mcp_servers={},
        max_turns=25,
        max_budget_usd=config.budgets.stage3_usd,
        cwd=str(cwd or Path.cwd()),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
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
    ranked = rank_probes(result.data.probes, gap_models)
    print(f"[stage-3] probes_raw={len(result.data.probes)} probes_ranked={len(ranked)}", file=sys.stderr, flush=True)

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

    result.data.probes = diversified[: config.generation.max_probes_surfaced]
    result.data.probe_count = len(result.data.probes)
    print(
        f"[stage-3] probes_final={result.data.probe_count} "
        f"(max_surfaced={config.generation.max_probes_surfaced})",
        file=sys.stderr,
        flush=True,
    )

    if context.warnings:
        result.data.warnings.extend(context.warnings)
    result.extras = {"prompt_tokens": prompt_tokens}
    return result
