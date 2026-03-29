from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.models import CoverageGapManifest
from parity.prompts.stage2_template import render_stage2_prompt
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema

_STAGE2_INJECT_KEYS = {"run_id", "stage1_run_id", "timestamp", "schema_version"}


def _dedupe_non_empty(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized or normalized in deduped:
            continue
        deduped.append(normalized)
    return deduped


def _build_stage2_mapping_resolutions(
    stage1_manifest: dict,
    config: ParityConfig,
) -> list[dict[str, Any]]:
    resolutions: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()

    for change in stage1_manifest.get("changes", []):
        artifact_path = change.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path or artifact_path in seen_artifacts:
            continue
        seen_artifacts.add(artifact_path)

        mapping = config.find_mapping(artifact_path)
        resolution: dict[str, Any] = {
            "artifact_path": artifact_path,
            "artifact_class": change.get("artifact_class"),
        }
        if mapping is None:
            resolution.update(
                {
                    "mapping_status": "unresolved",
                    "resolution_source": "none",
                    "platform": None,
                    "target": None,
                    "project": None,
                    "eval_type": None,
                    "access_mode": None,
                }
            )
        else:
            target = mapping.dataset
            access_mode = "mcp"
            if mapping.platform == "promptfoo":
                target = mapping.dataset or (
                    config.platforms.promptfoo.config_path if config.platforms.promptfoo else None
                )
                access_mode = "file"
            resolution.update(
                {
                    "mapping_status": "explicit",
                    "resolution_source": "parity_yaml",
                    "platform": mapping.platform,
                    "target": target,
                    "project": mapping.project,
                    "eval_type": mapping.eval_type,
                    "access_mode": access_mode,
                }
            )
        resolutions.append(resolution)

    return resolutions


def _build_stage2_bootstrap_brief(stage1_manifest: dict) -> dict[str, Any]:
    change_briefs: list[dict[str, Any]] = []
    for change in stage1_manifest.get("changes", []):
        artifact_path = change.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            continue

        risk_flags = _dedupe_non_empty(
            [
                *change.get("unintended_risk_flags", []),
                *change.get("false_negative_risks", []),
                *change.get("false_positive_risks", []),
            ]
        )
        change_briefs.append(
            {
                "artifact_path": artifact_path,
                "artifact_class": change.get("artifact_class"),
                "inferred_intent": change.get("inferred_intent"),
                "affected_components": change.get("affected_components", []),
                "risk_flags": risk_flags,
            }
        )

    return {
        "overall_risk": stage1_manifest.get("overall_risk"),
        "compound_change_detected": bool(stage1_manifest.get("compound_change_detected")),
        "changes": change_briefs,
    }


def _normalize_stage2_payload(payload: dict[str, Any]) -> dict[str, Any]:
    coverage_summary = payload.get("coverage_summary")
    if not isinstance(coverage_summary, dict):
        return payload

    if coverage_summary.get("mode") == "coverage_aware" and coverage_summary.get("bootstrap_reason"):
        coverage_summary.setdefault("retrieval_notes", coverage_summary["bootstrap_reason"])
        coverage_summary.pop("bootstrap_reason", None)
    return payload


def run_stage2(
    stage1_manifest: dict,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
    mcp_servers: str | Path | dict | None = None,
) -> StageRunResult:
    run_id = f"stage2-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mapping_resolutions = _build_stage2_mapping_resolutions(stage1_manifest, config)
    bootstrap_brief = _build_stage2_bootstrap_brief(stage1_manifest)
    prompt = render_stage2_prompt(
        stage1_manifest,
        mapping_resolutions=mapping_resolutions,
        bootstrap_brief=bootstrap_brief,
    )

    change_count = len(stage1_manifest.get("changes", []))
    explicit_mapping_count = sum(
        1 for resolution in mapping_resolutions if resolution.get("mapping_status") == "explicit"
    )
    unresolved_mapping_count = sum(
        1 for resolution in mapping_resolutions if resolution.get("mapping_status") == "unresolved"
    )
    mcp_configured = isinstance(mcp_servers, (str, Path)) or (
        isinstance(mcp_servers, dict) and bool(mcp_servers)
    )
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-2] changes_from_stage1={change_count} explicit_mappings={explicit_mapping_count} "
        f"unresolved_mappings={unresolved_mapping_count} mcp_configured={mcp_configured} "
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
            normalize_payload=_normalize_stage2_payload,
        )
    )
    gap_count = len(getattr(result.data, "gaps", []))
    print(f"[stage-2] gaps_identified={gap_count}", file=sys.stderr, flush=True)
    coverage_summary = getattr(result.data, "coverage_summary", None)
    if coverage_summary is not None:
        source = ":".join(
            bit for bit in [coverage_summary.platform, coverage_summary.dataset] if bit
        ) or "none"
        retrieval_notes = coverage_summary.retrieval_notes or coverage_summary.bootstrap_reason or "none"
        retrieval_preview = retrieval_notes.replace("\n", " ").strip()[:160]
        print(
            f"[stage-2] retrieval_path: mode={coverage_summary.mode} corpus_status={coverage_summary.corpus_status} "
            f"source={source} notes={retrieval_preview}",
            file=sys.stderr,
            flush=True,
        )
    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
        "explicit_mappings": explicit_mapping_count,
        "unresolved_mappings": unresolved_mapping_count,
    }
    return result
