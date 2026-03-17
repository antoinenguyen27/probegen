from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from pydantic.json_schema import model_json_schema

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
    run_id = f"stage1-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = render_stage1_prompt(raw_change_data, context, run_id=run_id, timestamp=timestamp)

    # Generate JSON schema for structured output validation
    output_schema = model_json_schema(
        BehaviorChangeManifest,
        mode="serialization",
        by_alias=True,
    )

    options = ClaudeAgentOptions(
        allowed_tools=["Bash", "Read", "Glob"],  # Bash needed: agent uses git show/diff for non-pre-loaded files
        mcp_servers={},
        max_turns=40,
        max_budget_usd=config.budgets.stage1_usd,
        cwd=str(cwd or Path.cwd()),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
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
