from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from parity.config import ParityConfig, ResolvedSpendCaps
from parity.context import count_tokens
from parity.errors import BudgetExceededError
from parity.models import (
    CoverageGap,
    CoverageTargetSummary,
    EvalAnalysisManifest,
    EvalMethodProfile,
    EvalTargetProfile,
    ResolvedEvalTarget,
)
from parity.prompts.stage2_template import render_stage2_prompt
from parity.stages._common import StageRunResult, run_stage_with_retry, simplify_schema
from parity.stages.security import build_stage2_options
from parity.stages.stage2_mcp import build_stage2_mcp_server

_STAGE2_INJECT_KEYS = {"run_id", "stage1_run_id", "timestamp", "schema_version", "runtime_metadata"}


def _dedupe_non_empty(values: list[Any]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _build_stage2_rule_resolutions(stage1_manifest: dict, config: ParityConfig) -> list[dict[str, Any]]:
    resolutions: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    for change in stage1_manifest.get("changes", []):
        artifact_path = change.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path or artifact_path in seen_artifacts:
            continue
        seen_artifacts.add(artifact_path)
        rule = config.find_eval_rule(artifact_path)
        if rule is None:
            resolutions.append(
                {
                    "artifact_path": artifact_path,
                    "artifact_class": change.get("artifact_class"),
                    "rule_status": "unresolved",
                    "preferred_platform": None,
                    "preferred_target": None,
                    "preferred_project": None,
                    "allowed_methods": [],
                    "preferred_methods": [],
                    "repo_asset_hints": [],
                    "discovery_order": config.resolve_platform_discovery_order(),
                }
            )
            continue
        resolutions.append(
            {
                "artifact_path": artifact_path,
                "artifact_class": change.get("artifact_class"),
                "rule_status": "explicit",
                "preferred_platform": rule.preferred_platform,
                "preferred_target": rule.preferred_target,
                "preferred_project": rule.preferred_project,
                "allowed_methods": rule.allowed_methods,
                "preferred_methods": rule.preferred_methods,
                "repo_asset_hints": rule.repo_asset_hints,
                "discovery_order": config.resolve_platform_discovery_order(rule.preferred_platform),
            }
        )
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
        changed_entities = [
            entity.model_dump(mode="json") if hasattr(entity, "model_dump") else entity
            for entity in change.get("changed_entities", [])
        ]
        evidence_snippets = [
            snippet.model_dump(mode="json") if hasattr(snippet, "model_dump") else snippet
            for snippet in change.get("evidence_snippets", [])
        ]
        observable_delta = change.get("observable_delta")
        if hasattr(observable_delta, "model_dump"):
            observable_delta = observable_delta.model_dump(mode="json")
        change_briefs.append(
            {
                "artifact_path": artifact_path,
                "artifact_class": change.get("artifact_class"),
                "inferred_intent": change.get("inferred_intent"),
                "change_summary": change.get("change_summary"),
                "affected_components": change.get("affected_components", []),
                "risk_flags": risk_flags,
                "behavioral_signatures": change.get("behavioral_signatures", []),
                "changed_entities": changed_entities,
                "observable_delta": observable_delta,
                "eval_search_hints": change.get("eval_search_hints", []),
                "validation_focus": change.get("validation_focus", []),
                "evidence_snippets": evidence_snippets,
            }
        )
    return {
        "overall_risk": stage1_manifest.get("overall_risk"),
        "compound_change_detected": bool(stage1_manifest.get("compound_change_detected")),
        "changes": change_briefs,
    }


def _build_bootstrap_target(change: dict[str, Any], reason: str) -> ResolvedEvalTarget:
    artifact_path = change.get("artifact_path", "unknown")
    target_id = f"bootstrap::{artifact_path}"
    profile = EvalTargetProfile(
        target_id=target_id,
        platform="bootstrap",
        locator=artifact_path,
        target_name=f"Bootstrap target for {artifact_path}",
        artifact_paths=[artifact_path],
        resolution_source="bootstrap",
        access_mode="synthetic",
        write_capability="review_only",
        profile_confidence=0.0,
    )
    method_profile = EvalMethodProfile(
        method_kind="unknown",
        input_shape="unknown",
        assertion_style="unknown",
        renderability_status="review_only",
        confidence=0.0,
        notes=[reason],
    )
    return ResolvedEvalTarget(
        profile=profile,
        method_profile=method_profile,
        samples=[],
        raw_field_patterns=[],
        aggregate_method_hints=[],
        resolution_notes=[reason],
    )


def _build_recovered_target_from_snapshot(snapshot_payload: dict[str, Any], reason: str) -> ResolvedEvalTarget | None:
    if not isinstance(snapshot_payload, dict):
        return None

    target_id = snapshot_payload.get("target_id")
    platform = snapshot_payload.get("platform")
    target_name = snapshot_payload.get("target_name")
    locator = snapshot_payload.get("target_locator") or snapshot_payload.get("target")
    method_profile = snapshot_payload.get("method_profile")
    if not all(isinstance(value, str) and value for value in (target_id, platform, target_name, locator)):
        return None
    if not isinstance(method_profile, dict):
        return None

    resolution_source = "repo_asset_discovery" if platform == "promptfoo" else "platform_discovery"
    access_mode = "file" if platform == "promptfoo" else "mcp"
    try:
        target = ResolvedEvalTarget.model_validate(
            {
                "profile": {
                    "target_id": target_id,
                    "platform": platform,
                    "locator": locator,
                    "target_name": target_name,
                    "dataset_id": snapshot_payload.get("dataset_id"),
                    "project": snapshot_payload.get("project"),
                    "artifact_paths": snapshot_payload.get("artifact_paths", []),
                    "resolution_source": resolution_source,
                    "access_mode": access_mode,
                    "write_capability": method_profile.get("renderability_status", "unsupported"),
                    "profile_confidence": snapshot_payload.get(
                        "profile_confidence",
                        method_profile.get("confidence", 0.0),
                    ),
                },
                "method_profile": method_profile,
                "samples": snapshot_payload.get("samples", []),
                "evaluator_dossiers": snapshot_payload.get("evaluator_dossiers", []),
                "raw_field_patterns": snapshot_payload.get("raw_field_patterns", []),
                "aggregate_method_hints": snapshot_payload.get("aggregate_method_hints", []),
                "resolution_notes": [
                    "Recovered from cached Stage 2 snapshot after degraded finalization.",
                    reason,
                ],
            }
        )
    except Exception:
        return None
    return target


def _coerce_cached_stage2_targets(
    cached_target_snapshots: list[dict[str, Any]] | None,
    *,
    reason: str,
) -> list[ResolvedEvalTarget]:
    if not isinstance(cached_target_snapshots, list):
        return []
    valid: list[ResolvedEvalTarget] = []
    seen: set[str] = set()
    for snapshot_payload in cached_target_snapshots:
        target = _build_recovered_target_from_snapshot(snapshot_payload, reason)
        if target is None or target.profile.target_id in seen:
            continue
        seen.add(target.profile.target_id)
        valid.append(target)
    return valid


def _artifact_target_lookup(resolved_targets: list[ResolvedEvalTarget]) -> dict[str, ResolvedEvalTarget]:
    lookup: dict[str, ResolvedEvalTarget] = {}
    for target in resolved_targets:
        for artifact_path in target.profile.artifact_paths:
            if isinstance(artifact_path, str) and artifact_path and artifact_path not in lookup:
                lookup[artifact_path] = target
    return lookup


def _coerce_partial_stage2_targets(partial_payload: dict[str, Any] | None) -> list[ResolvedEvalTarget]:
    if not isinstance(partial_payload, dict):
        return []
    raw_targets = partial_payload.get("resolved_targets")
    if not isinstance(raw_targets, list):
        return []
    valid: list[ResolvedEvalTarget] = []
    seen: set[str] = set()
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict):
            continue
        try:
            target = ResolvedEvalTarget.model_validate(raw_target)
        except Exception:
            continue
        if target.profile.target_id in seen:
            continue
        seen.add(target.profile.target_id)
        valid.append(target)
    return valid


def _coerce_partial_stage2_gaps(partial_payload: dict[str, Any] | None) -> list[CoverageGap]:
    if not isinstance(partial_payload, dict):
        return []
    raw_gaps = partial_payload.get("gaps")
    if not isinstance(raw_gaps, list):
        return []
    valid_gaps: list[CoverageGap] = []
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
        valid_gaps.append(gap)
    return valid_gaps


def _coerce_partial_stage2_coverage(partial_payload: dict[str, Any] | None) -> list[CoverageTargetSummary]:
    if not isinstance(partial_payload, dict):
        return []
    raw_items = partial_payload.get("coverage_by_target")
    if not isinstance(raw_items, list):
        return []
    valid: list[CoverageTargetSummary] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        try:
            summary = CoverageTargetSummary.model_validate(raw_item)
        except Exception:
            continue
        if summary.target_id in seen:
            continue
        seen.add(summary.target_id)
        valid.append(summary)
    return valid


def _infer_guardrail_direction(change: dict[str, Any], risk_flag: str) -> str | None:
    if risk_flag in _dedupe_non_empty(change.get("false_negative_risks", [])):
        return "should_catch"
    if risk_flag in _dedupe_non_empty(change.get("false_positive_risks", [])):
        return "should_pass"
    return None


def _build_stage2_fallback_gaps(
    stage1_manifest: dict,
    reason: str,
    *,
    resolved_targets: list[ResolvedEvalTarget] | None = None,
) -> list[CoverageGap]:
    gaps: list[CoverageGap] = []
    overall_risk = stage1_manifest.get("overall_risk") or "medium"
    artifact_lookup = _artifact_target_lookup(resolved_targets or [])
    for change_index, change in enumerate(stage1_manifest.get("changes", []), start=1):
        artifact_path = change.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            continue
        resolved_target = artifact_lookup.get(artifact_path)
        target_id = resolved_target.profile.target_id if resolved_target is not None else f"bootstrap::{artifact_path}"
        method_kind = resolved_target.method_profile.method_kind if resolved_target is not None else "unknown"
        description = (
            "Existing target evidence was recovered, but full coverage analysis did not complete before the fallback."
            if resolved_target is not None
            else "Bootstrap this behavior as a new eval area because analysis did not complete."
        )
        existing_coverage_notes = (
            "Recovered native target evidence is available, but the gap was not fully classified before the fallback."
            if resolved_target is not None
            else "No validated native corpus comparison was completed before the fallback."
        )
        recommended_eval_mode = resolved_target.method_profile.method_kind if resolved_target is not None else "unknown"
        profile_status = "uncertain" if resolved_target is not None else "bootstrap"
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
                CoverageGap(
                    gap_id=f"{target_id}::gap::{change_index:03d}:{risk_index:02d}",
                    artifact_path=artifact_path,
                    target_id=target_id,
                    method_kind=method_kind,
                    gap_type="uncovered",
                    related_risk_flag=risk_flag,
                    description=description,
                    why_gap_is_real=reason,
                    existing_coverage_notes=existing_coverage_notes,
                    recommended_eval_area=change.get("artifact_class") or "behavior_regression",
                    recommended_eval_mode=recommended_eval_mode,
                    native_shape_hints=list(change.get("validation_focus", [])),
                    compatible_nearest_cases=[],
                    repo_asset_refs=[],
                    priority=overall_risk,
                    profile_status=profile_status,
                    guardrail_direction=_infer_guardrail_direction(change, risk_flag),
                    is_conversational=False,
                    confidence=0.0,
                )
            )
    return gaps


def _build_stage2_fallback_coverage(
    stage1_manifest: dict,
    resolved_targets: list[ResolvedEvalTarget],
    reason: str,
) -> list[CoverageTargetSummary]:
    summaries: list[CoverageTargetSummary] = []
    represented_artifacts: set[str] = set()
    for target in resolved_targets:
        sample_count = len(target.samples)
        represented_artifacts.update(
            artifact_path
            for artifact_path in target.profile.artifact_paths
            if isinstance(artifact_path, str) and artifact_path
        )
        summaries.append(
            CoverageTargetSummary(
                target_id=target.profile.target_id,
                method_kind=target.method_profile.method_kind,
                total_relevant_cases=sample_count,
                cases_covering_changed_behavior=0,
                coverage_ratio=0.0,
                mode="bootstrap" if sample_count == 0 or target.profile.platform == "bootstrap" else "coverage_aware",
                corpus_status="empty" if sample_count == 0 else "available",
                profile_status="bootstrap" if sample_count == 0 or target.profile.platform == "bootstrap" else "uncertain",
                retrieval_notes=reason if sample_count > 0 else None,
                bootstrap_reason=reason if sample_count == 0 or target.profile.platform == "bootstrap" else None,
                analysis_notes=[],
            )
        )
    for change in stage1_manifest.get("changes", []):
        artifact_path = change.get("artifact_path")
        if not isinstance(artifact_path, str) or artifact_path in represented_artifacts:
            continue
        summaries.append(
            CoverageTargetSummary(
                target_id=f"bootstrap::{artifact_path}",
                method_kind="unknown",
                total_relevant_cases=0,
                cases_covering_changed_behavior=0,
                coverage_ratio=0.0,
                mode="bootstrap",
                corpus_status="unavailable",
                profile_status="bootstrap",
                bootstrap_reason=reason,
                analysis_notes=[],
            )
        )
    return summaries


def _build_stage2_degraded_reason(exc: BudgetExceededError) -> str:
    subtype = ""
    if isinstance(exc.details, dict):
        raw_subtype = exc.details.get("subtype")
        if isinstance(raw_subtype, str):
            subtype = raw_subtype

    if subtype == "error_max_turns":
        return (
            "Stage 2 max-turn limit was reached before full eval analysis completed. "
            "Returning a degraded analysis manifest from recovered discovery evidence and bootstrap fallback where needed."
        )
    if exc.message == "Rate limit persisted after retries":
        return (
            "Stage 2 hit a persistent rate limit before full eval analysis completed. "
            "Returning a degraded analysis manifest from recovered discovery evidence and bootstrap fallback where needed."
        )
    return (
        "Stage 2 spend cap was exhausted before full eval analysis completed. "
        "Returning a degraded analysis manifest from recovered discovery evidence and bootstrap fallback where needed."
    )


def _coerce_partial_stage2_manifest(
    *,
    partial_payload: dict[str, Any] | None,
    run_id: str,
    stage1_manifest: dict,
    timestamp: str,
    runtime_metadata: dict[str, Any],
    degraded_reason: str | None = None,
) -> EvalAnalysisManifest | None:
    if not isinstance(partial_payload, dict):
        return None
    candidate = dict(partial_payload)
    candidate["run_id"] = run_id
    candidate["stage1_run_id"] = stage1_manifest.get("run_id", "")
    candidate["timestamp"] = timestamp
    candidate["runtime_metadata"] = runtime_metadata
    if degraded_reason is not None:
        candidate["analysis_status"] = "degraded"
        candidate["degradation_reason"] = degraded_reason
    candidate.setdefault(
        "unresolved_artifacts",
        _derive_unresolved_artifacts(
            stage1_manifest=stage1_manifest,
            resolved_targets=_coerce_partial_stage2_targets(partial_payload),
        ),
    )
    try:
        return EvalAnalysisManifest.model_validate(candidate)
    except Exception:
        return None


def _derive_unresolved_artifacts(
    *,
    stage1_manifest: dict,
    resolved_targets: list[ResolvedEvalTarget],
) -> list[str]:
    changed_artifacts = [
        change.get("artifact_path")
        for change in stage1_manifest.get("changes", [])
        if isinstance(change.get("artifact_path"), str)
    ]
    resolved_artifacts: set[str] = set()
    for target in resolved_targets:
        if target.profile.platform == "bootstrap":
            continue
        for artifact_path in target.profile.artifact_paths:
            if isinstance(artifact_path, str) and artifact_path:
                resolved_artifacts.add(artifact_path)
    return [artifact for artifact in changed_artifacts if artifact not in resolved_artifacts]


def _build_stage2_budget_fallback(
    *,
    stage1_manifest: dict,
    run_id: str,
    timestamp: str,
    runtime_metadata: dict[str, Any],
    reason: str,
    partial_payload: dict[str, Any] | None = None,
    cached_target_snapshots: list[dict[str, Any]] | None = None,
) -> EvalAnalysisManifest:
    partial_manifest = _coerce_partial_stage2_manifest(
        partial_payload=partial_payload,
        run_id=run_id,
        stage1_manifest=stage1_manifest,
        timestamp=timestamp,
        runtime_metadata=runtime_metadata,
        degraded_reason=reason,
    )
    if partial_manifest is not None:
        return partial_manifest

    partial_targets = _coerce_partial_stage2_targets(partial_payload)
    cached_targets = _coerce_cached_stage2_targets(cached_target_snapshots, reason=reason)
    resolved_targets = list(partial_targets)
    seen_target_ids = {target.profile.target_id for target in resolved_targets}
    for target in cached_targets:
        if target.profile.target_id in seen_target_ids:
            continue
        seen_target_ids.add(target.profile.target_id)
        resolved_targets.append(target)
    if not resolved_targets:
        resolved_targets = [
            _build_bootstrap_target(change, reason)
            for change in stage1_manifest.get("changes", [])
            if isinstance(change.get("artifact_path"), str)
        ]
    resolved_target_ids = {target.profile.target_id for target in resolved_targets}
    partial_gaps = [
        gap
        for gap in _coerce_partial_stage2_gaps(partial_payload)
        if gap.target_id in resolved_target_ids
    ]
    gaps = partial_gaps or _build_stage2_fallback_gaps(
        stage1_manifest,
        reason,
        resolved_targets=resolved_targets,
    )
    partial_coverage = [
        summary
        for summary in _coerce_partial_stage2_coverage(partial_payload)
        if summary.target_id in resolved_target_ids
    ]
    coverage_by_target = partial_coverage or _build_stage2_fallback_coverage(
        stage1_manifest,
        resolved_targets,
        reason,
    )
    unresolved_artifacts = _derive_unresolved_artifacts(
        stage1_manifest=stage1_manifest,
        resolved_targets=resolved_targets,
    )
    return EvalAnalysisManifest.model_validate(
        {
            "run_id": run_id,
            "stage1_run_id": stage1_manifest.get("run_id", ""),
            "timestamp": timestamp,
            "analysis_status": "degraded",
            "degradation_reason": reason,
            "unresolved_artifacts": unresolved_artifacts,
            "resolved_targets": [target.model_dump(mode="json") for target in resolved_targets],
            "coverage_by_target": [summary.model_dump(mode="json") for summary in coverage_by_target],
            "gaps": [gap.model_dump(mode="json") for gap in gaps],
            "runtime_metadata": runtime_metadata,
        }
    )


def run_stage2(
    stage1_manifest: dict,
    config: ParityConfig,
    *,
    cwd: str | Path | None = None,
    resolved_spend: ResolvedSpendCaps | None = None,
) -> StageRunResult:
    run_id = f"stage2-{int(time.time())}"
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resolved_spend = resolved_spend or config.resolve_spend_caps()
    rule_resolutions = _build_stage2_rule_resolutions(stage1_manifest, config)
    bootstrap_brief = _build_stage2_bootstrap_brief(stage1_manifest)
    prompt = render_stage2_prompt(
        stage1_manifest,
        rule_resolutions=rule_resolutions,
        bootstrap_brief=bootstrap_brief,
    )

    change_count = len(stage1_manifest.get("changes", []))
    explicit_rule_count = sum(1 for resolution in rule_resolutions if resolution.get("rule_status") == "explicit")
    unresolved_rule_count = sum(1 for resolution in rule_resolutions if resolution.get("rule_status") == "unresolved")
    prompt_tokens = count_tokens(prompt)
    print(
        f"[stage-2] changes_from_stage1={change_count} explicit_rules={explicit_rule_count} "
        f"unresolved_rules={unresolved_rule_count} prompt_tokens={prompt_tokens}",
        file=sys.stderr,
        flush=True,
    )

    output_schema = simplify_schema(
        EvalAnalysisManifest.model_json_schema(),
        remove_keys=_STAGE2_INJECT_KEYS,
    )

    repo_root = Path(cwd or Path.cwd()).resolve()
    stage2_runtime = build_stage2_mcp_server(
        config=config,
        repo_root=repo_root,
        env=dict(os.environ),
        embedding_spend_cap_usd=resolved_spend.stage2_embedding_cap_usd,
    )
    options = build_stage2_options(
        cwd=str(repo_root),
        max_turns=40,
        max_budget_usd=resolved_spend.stage2_agent_cap_usd,
        output_schema=output_schema,
        mcp_servers={
            "parity_stage2": {
                "type": "sdk",
                "name": "parity-stage2",
                "instance": stage2_runtime.server._mcp_server,
            }
        },
    )

    degraded_reason: str | None = None
    try:
        result = asyncio.run(
            run_stage_with_retry(
                stage_num=2,
                prompt=prompt,
                options=options,
                output_model=EvalAnalysisManifest,
                inject_fields={
                    "run_id": run_id,
                    "stage1_run_id": stage1_manifest.get("run_id", ""),
                    "timestamp": timestamp,
                },
            )
        )
    except BudgetExceededError as exc:
        degraded_reason = _build_stage2_degraded_reason(exc)
        print(f"[stage-2] degraded_fallback: {degraded_reason}", file=sys.stderr, flush=True)
        exc_details = exc.details if isinstance(exc.details, dict) else {}
        result = StageRunResult(
            data=_build_stage2_budget_fallback(
                stage1_manifest=stage1_manifest,
                run_id=run_id,
                timestamp=timestamp,
                runtime_metadata=stage2_runtime.toolbox.build_runtime_metadata(),
                reason=degraded_reason,
                partial_payload=exc.partial_result if isinstance(exc.partial_result, dict) else None,
                cached_target_snapshots=stage2_runtime.toolbox.build_recovery_state().get("cached_target_snapshots"),
            ),
            model=exc_details.get("model") if isinstance(exc_details.get("model"), str) else None,
            cost_usd=exc.cost_usd,
            duration_ms=int(exc_details.get("duration_ms", 0) or 0),
            num_turns=int(exc_details.get("num_turns", 0) or 0),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            raw_result=None,
            extras={
                "assistant_messages": exc_details.get("assistant_messages", 0),
                "observed_tool_uses": exc_details.get("observed_tool_uses", 0),
                "tools_observed": exc_details.get("tools_observed", []),
                "failure_subtype": exc_details.get("subtype"),
            },
        )

    runtime_metadata = stage2_runtime.toolbox.build_runtime_metadata()
    result.data.runtime_metadata = runtime_metadata
    target_count = len(result.data.resolved_targets)
    gap_count = len(result.data.gaps)
    print(
        f"[stage-2] targets_resolved={target_count} gaps_identified={gap_count}",
        file=sys.stderr,
        flush=True,
    )
    for target in result.data.resolved_targets[:5]:
        print(
            f"[stage-2] target={target.profile.target_id} platform={target.profile.platform} "
            f"method={target.method_profile.method_kind} renderability={target.method_profile.renderability_status} "
            f"samples={len(target.samples)}",
            file=sys.stderr,
            flush=True,
        )

    result.extras = {
        **(result.extras or {}),
        "prompt_tokens": prompt_tokens,
        "explicit_rules": explicit_rule_count,
        "unresolved_rules": unresolved_rule_count,
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
