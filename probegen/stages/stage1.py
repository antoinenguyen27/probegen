from __future__ import annotations

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from probegen.config import ProbegenConfig
from probegen.context import count_tokens
from probegen.models import BehaviorChangeManifest
from probegen.prompts.stage1_template import render_stage1_prompt
from probegen.stages._common import StageRunResult, run_stage_with_retry


def run_stage1(
    raw_change_data: dict,
    context,
    config: ProbegenConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    prompt = render_stage1_prompt(raw_change_data, context)
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob"],  # Bash excluded: Stage 1 is analysis-only
        mcp_servers={},
        max_turns=30,
        max_budget_usd=config.budgets.stage1_usd,
        cwd=str(cwd or Path.cwd()),
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=1,
            prompt=prompt,
            options=options,
            output_model=BehaviorChangeManifest,
        )
    )
    result.extras = {"prompt_tokens": count_tokens(prompt)}
    return result
