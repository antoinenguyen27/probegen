from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.models import BehaviorChangeManifest
from parity.prompts.stage1_template import render_stage1_prompt
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema
from parity.stages.security import build_stage1_options

_STAGE1_INJECT_KEYS = {"run_id", "pr_number", "commit_sha", "timestamp", "schema_version"}


def run_stage1(
    raw_change_data: dict,
    context,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    run_id = f"stage1-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_spend = config.resolve_spend_caps()
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

    options = build_stage1_options(
        cwd=str(cwd or Path.cwd()),
        max_turns=40,
        max_budget_usd=resolved_spend.stage1_agent_cap_usd,
        output_schema=output_schema,
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
    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
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
