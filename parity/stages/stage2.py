from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.models import CoverageGapManifest
from parity.prompts.stage2_template import render_stage2_prompt
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema

_STAGE2_INJECT_KEYS = {"run_id", "stage1_run_id", "timestamp", "schema_version"}


def run_stage2(
    stage1_manifest: dict,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
    mcp_servers: str | Path | dict | None = None,
) -> StageRunResult:
    run_id = f"stage2-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = render_stage2_prompt(stage1_manifest)

    behavior_count = len(stage1_manifest.get("behaviors", []))
    mcp_configured = isinstance(mcp_servers, (str, Path)) or (
        isinstance(mcp_servers, dict) and bool(mcp_servers)
    )
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-2] behaviors_from_stage1={behavior_count} mcp_configured={mcp_configured} "
        f"prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        CoverageGapManifest.model_json_schema(),
        remove_keys=_STAGE2_INJECT_KEYS,
    )

    options = ClaudeAgentOptions(
        allowed_tools=[],  # empty = all tools permitted, including MCP servers and Bash.
                           # Stage 2 needs both Bash (for embed_batch, find_similar) and MCP (for platform queries).
        mcp_servers=mcp_servers or {},
        max_turns=40,
        max_budget_usd=config.budgets.stage2_usd,
        cwd=str(cwd or Path.cwd()),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
    )
    result = asyncio.run(
        run_stage_with_retry(
            stage_num=2,
            prompt=prompt,
            options=options,
            output_model=CoverageGapManifest,
            inject_fields={
                "run_id": run_id,
                "stage1_run_id": stage1_manifest.get("run_id", ""),
                "timestamp": timestamp,
            },
        )
    )
    gap_count = len(getattr(result.data, "gaps", []))
    print(f"[stage-2] gaps_identified={gap_count}", file=sys.stderr, flush=True)
    result.extras = {"prompt_tokens": prompt_tokens}
    return result
