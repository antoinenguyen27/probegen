from __future__ import annotations

import asyncio
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from parity.config import ParityConfig
from parity.context import count_tokens
from parity.errors import BudgetExceededError
from parity.models import CoverageGap, CoverageGapManifest, CoverageSummary
from parity.prompts.stage2_template import render_stage2_prompt
from parity.stages.stage2_mcp import build_stage2_mcp_server
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


def _build_stage2_unmapped_artifacts(mapping_resolutions: list[dict[str, Any]]) -> list[str]:
    return [
        resolution["artifact_path"]
        for resolution in mapping_resolutions
        if resolution.get("mapping_status") == "unresolved" and isinstance(resolution.get("artifact_path"), str)
    ]


def _infer_guardrail_direction(change: dict[str, Any], risk_flag: str) -> str | None:
    if risk_flag in _dedupe_non_empty(change.get("false_negative_risks", [])):
        return "should_catch"
    if risk_flag in _dedupe_non_empty(change.get("false_positive_risks", [])):
        return "should_pass"
    return None


def _build_stage2_fallback_gaps(stage1_manifest: dict) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    overall_risk = stage1_manifest.get("overall_risk") or "medium"
    for change_index, change in enumerate(stage1_manifest.get("changes", []), start=1):
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
        if not risk_flags:
            fallback_flag = change.get("change_summary") or change.get("inferred_intent") or "Behavior changed"
            risk_flags = [str(fallback_flag)]
        for risk_index, risk_flag in enumerate(risk_flags, start=1):
            gaps.append(
                {
                    "gap_id": f"gap_bootstrap_{change_index:03d}_{risk_index:02d}",
                    "artifact_path": artifact_path,
                    "gap_type": "uncovered",
                    "related_risk_flag": risk_flag,
                    "description": (
                        "Coverage analysis was degraded before full corpus comparison completed. "
                        "Treat this as a bootstrap proposal target for the predicted risk."
                    ),
                    "nearest_existing_cases": [],
                    "priority": overall_risk,
                    "guardrail_direction": _infer_guardrail_direction(change, risk_flag),
                    "is_conversational": False,
                }
            )
    return gaps


def _build_stage2_fallback_coverage_summary(
    *,
    mapping_resolutions: list[dict[str, Any]],
    runtime_metadata: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    retrieval = runtime_metadata.get("retrieval", {})
    embedding = runtime_metadata.get("embedding", {})
    sources = retrieval.get("sources", []) if isinstance(retrieval, dict) else []
    total_cases = int(retrieval.get("total_cases", 0) or 0) if isinstance(retrieval, dict) else 0
    blocked_requests = int(embedding.get("blocked_request_count", 0) or 0) if isinstance(embedding, dict) else 0
    source_platform = None
    source_target = None
    if len(sources) == 1 and isinstance(sources[0], dict):
        source_platform = sources[0].get("platform")
        source_target = sources[0].get("target")

    if total_cases > 0:
        notes: list[str] = [reason]
        if sources:
            source_preview = ", ".join(
                f"{source.get('platform')}:{source.get('target')}" for source in sources[:3] if isinstance(source, dict)
            )
            if source_preview:
                if len(sources) > 3:
                    source_preview = f"{source_preview}, ..."
                notes.append(f"Retrieved {total_cases} eval case(s) from {source_preview}.")
        if blocked_requests:
            notes.append(f"Embedding spend cap blocked {blocked_requests} embedding request(s).")
        return {
            "total_relevant_cases": total_cases,
            "cases_covering_changed_behavior": 0,
            "coverage_ratio": 0.0,
            "platform": source_platform,
            "dataset": source_target,
            "mode": "coverage_aware",
            "corpus_status": "available",
            "retrieval_notes": " ".join(notes).strip(),
        }

    unmapped_artifacts = _build_stage2_unmapped_artifacts(mapping_resolutions)
    bootstrap_reason = reason
    if blocked_requests:
        bootstrap_reason = f"{bootstrap_reason} Embedding spend cap blocked {blocked_requests} embedding request(s)."
    if unmapped_artifacts:
        bootstrap_reason = f"{bootstrap_reason} Unmapped artifacts: {', '.join(unmapped_artifacts)}."
    fetch_requests = int(retrieval.get("fetch_request_count", 0) or 0) if isinstance(retrieval, dict) else 0
    corpus_status = "empty" if fetch_requests > 0 else "unavailable"
    return {
        "total_relevant_cases": total_cases,
        "cases_covering_changed_behavior": 0,
        "coverage_ratio": 0.0,
        "platform": source_platform,
        "dataset": source_target,
        "mode": "bootstrap",
        "corpus_status": corpus_status,
        "bootstrap_reason": bootstrap_reason.strip(),
    }


def _coerce_partial_stage2_coverage_summary(partial_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(partial_payload, dict):
        return None
    raw_coverage_summary = partial_payload.get("coverage_summary")
    if not isinstance(raw_coverage_summary, dict):
        return None
    normalized = _normalize_stage2_payload({"coverage_summary": deepcopy(raw_coverage_summary)})
    try:
        return CoverageSummary.model_validate(normalized["coverage_summary"]).model_dump(mode="json")
    except Exception:
        return None


def _coerce_partial_stage2_gaps(partial_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(partial_payload, dict):
        return []
    raw_gaps = partial_payload.get("gaps")
    if not isinstance(raw_gaps, list):
        return []
    valid_gaps: list[dict[str, Any]] = []
    seen_gap_ids: set[str] = set()
    for raw_gap in raw_gaps:
        if not isinstance(raw_gap, dict):
            continue
        try:
            gap = CoverageGap.model_validate(raw_gap)
        except Exception:
            continue
        if gap.gap_id in seen_gap_ids:
            continue
        seen_gap_ids.add(gap.gap_id)
        valid_gaps.append(gap.model_dump(mode="json"))
    return valid_gaps


def _build_stage2_budget_fallback(
    *,
    stage1_manifest: dict,
    mapping_resolutions: list[dict[str, Any]],
    run_id: str,
    timestamp: str,
    runtime_metadata: dict[str, Any],
    reason: str,
    partial_payload: dict[str, Any] | None = None,
) -> CoverageGapManifest:
    coverage_summary = _coerce_partial_stage2_coverage_summary(partial_payload) or _build_stage2_fallback_coverage_summary(
        mapping_resolutions=mapping_resolutions,
        runtime_metadata=runtime_metadata,
        reason=reason,
    )
    gaps = _coerce_partial_stage2_gaps(partial_payload) or _build_stage2_fallback_gaps(stage1_manifest)
    manifest_payload = {
        "run_id": run_id,
        "stage1_run_id": stage1_manifest.get("run_id", ""),
        "timestamp": timestamp,
        "unmapped_artifacts": _build_stage2_unmapped_artifacts(mapping_resolutions),
        "coverage_summary": coverage_summary,
        "gaps": gaps,
    }
    return CoverageGapManifest.model_validate(manifest_payload)


def run_stage2(
    stage1_manifest: dict,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
) -> StageRunResult:
    run_id = f"stage2-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_spend = config.resolve_spend_caps()
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
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-2] changes_from_stage1={change_count} explicit_mappings={explicit_mapping_count} "
        f"unresolved_mappings={unresolved_mapping_count} mcp_configured=True "
        f"prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        CoverageGapManifest.model_json_schema(),
        remove_keys=_STAGE2_INJECT_KEYS,
    )

    repo_root = Path(cwd or Path.cwd()).resolve()
    stage2_runtime = build_stage2_mcp_server(
        config=config,
        repo_root=repo_root,
        env=dict(os.environ),
        embedding_spend_cap_usd=resolved_spend.stage2_embedding_cap_usd,
    )
    options = ClaudeAgentOptions(
        tools=[],
        mcp_servers={
            "parity_stage2": {
                "type": "sdk",
                "name": "parity-stage2",
                # FastMCP wraps a low-level MCP server, and the Claude Agent SDK's
                # in-process transport expects that low-level server instance.
                "instance": stage2_runtime.server._mcp_server,
            }
        },
        max_turns=40,
        max_budget_usd=resolved_spend.stage2_agent_cap_usd,
        cwd=str(repo_root),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
    )
    degraded_reason: str | None = None
    try:
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
    except BudgetExceededError as exc:
        degraded_reason = (
            "Stage 2 agent spend cap was exhausted before full coverage analysis completed. "
            "Returning a degraded manifest derived from Stage 1 and any host-side retrieval state."
        )
        print(f"[stage-2] degraded_fallback: {degraded_reason}", file=sys.stderr, flush=True)
        result = StageRunResult(
            data=_build_stage2_budget_fallback(
                stage1_manifest=stage1_manifest,
                mapping_resolutions=mapping_resolutions,
                run_id=run_id,
                timestamp=timestamp,
                runtime_metadata=stage2_runtime.toolbox.build_runtime_metadata(),
                reason=degraded_reason,
                partial_payload=exc.partial_result if isinstance(exc.partial_result, dict) else None,
            ),
            model=None,
            cost_usd=exc.cost_usd,
            duration_ms=0,
            num_turns=0,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            raw_result=None,
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
    runtime_metadata = stage2_runtime.toolbox.build_runtime_metadata()
    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
        "explicit_mappings": explicit_mapping_count,
        "unresolved_mappings": unresolved_mapping_count,
        "resolved_spend_caps": {
            "analysis_total_spend_cap_usd": resolved_spend.analysis_total_spend_cap_usd,
            "stage1_agent_cap_usd": resolved_spend.stage1_agent_cap_usd,
            "stage2_agent_cap_usd": resolved_spend.stage2_agent_cap_usd,
            "stage2_embedding_cap_usd": resolved_spend.stage2_embedding_cap_usd,
            "stage3_agent_cap_usd": resolved_spend.stage3_agent_cap_usd,
            "source": resolved_spend.source,
        },
        "degraded": degraded_reason is not None,
        "degraded_reason": degraded_reason,
        **runtime_metadata,
    }
    return result
