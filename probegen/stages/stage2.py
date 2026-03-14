from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from probegen.config import ProbegenConfig
from probegen.context import count_tokens
from probegen.models import CoverageGapManifest
from probegen.prompts.stage2_template import render_stage2_prompt
from probegen.stages._common import StageRunResult, run_stage_with_retry


def run_stage2(
    stage1_manifest: dict,
    config: ProbegenConfig,
    *,
    cwd: str | Path | None = None,
    mcp_servers: str | Path | dict | None = None,
) -> StageRunResult:
    prompt = render_stage2_prompt(stage1_manifest)
    options = ClaudeAgentOptions(
        allowed_tools=["Bash"],
        mcp_servers=mcp_servers or {},
        max_turns=40,
        max_budget_usd=config.budgets.stage2_usd,
        cwd=str(cwd or Path.cwd()),
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=2,
            prompt=prompt,
            options=options,
            output_model=CoverageGapManifest,
        )
    )
    result.extras = {"prompt_tokens": count_tokens(prompt)}
    return result
