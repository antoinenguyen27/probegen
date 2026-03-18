from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from probegen.config import ProbegenConfig
from probegen.context import count_tokens
from probegen.models import BehaviorChangeManifest
from probegen.prompts.stage1_template import render_stage1_prompt
from probegen.stages._common import StageRunResult, run_stage_with_retry, simplify_schema

_STAGE1_INJECT_KEYS = {"run_id", "pr_number", "commit_sha", "timestamp", "schema_version"}


def run_stage1(
    raw_change_data: dict,
    context,
    config: ProbegenConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    run_id = f"stage1-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = render_stage1_prompt(raw_change_data, context)

    artifact_count = len(raw_change_data.get("hint_matched_artifacts", []))
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-1] Analyzing {artifact_count} hint-matched artifact(s) — prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        BehaviorChangeManifest.model_json_schema(),
        remove_keys=_STAGE1_INJECT_KEYS,
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
            inject_fields={
                "run_id": run_id,
                "pr_number": raw_change_data.get("pr_number"),
                "commit_sha": raw_change_data.get("head_sha", ""),
                "timestamp": timestamp,
            },
        )
    )
    result.extras = {"prompt_tokens": prompt_tokens}
    return result
