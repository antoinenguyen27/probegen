from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from probegen.config import ProbegenConfig
from probegen.context import count_tokens
from probegen.models import CoverageGap, ProbeProposal
from probegen.prompts.stage3_template import render_stage3_prompt
from probegen.stages._common import StageRunResult, build_metadata, run_stage_with_retry
from probegen.tools.similarity import apply_diversity_limit, rank_probes


def run_stage3(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context,
    config: ProbegenConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    prompt = render_stage3_prompt(
        stage1_manifest,
        stage2_manifest,
        context,
        max_probes_surfaced=config.generation.max_probes_surfaced,
    )
    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        mcp_servers={},
        max_turns=25,
        max_budget_usd=config.budgets.stage3_usd,
        cwd=str(cwd or Path.cwd()),
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=3,
            prompt=prompt,
            options=options,
            output_model=ProbeProposal,
        )
    )

    gap_models = [CoverageGap.model_validate(gap) for gap in stage2_manifest.get("gaps", [])]
    ranked = rank_probes(result.data.probes, gap_models)
    diversified = apply_diversity_limit(
        ranked,
        limit_per_gap=config.generation.diversity_limit_per_gap,
    )
    result.data.probes = diversified[: config.generation.max_probes_surfaced]
    result.data.probe_count = len(result.data.probes)
    if context.warnings:
        result.data.warnings.extend(context.warnings)
    result.extras = {"prompt_tokens": count_tokens(prompt)}
    return result
