from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import click

from parity.cli.get_behavior_diff import build_raw_change_data
from parity.config import ParityConfig, ResolvedSpendCaps
from parity.context import load_context_pack
from parity.errors import BudgetExceededError, ConfigError, GitDiffError, SchemaValidationError, StageError
from parity.export import write_run_artifacts
from parity.models import (
    BehaviorChangeManifest,
    EvalAnalysisManifest,
    EvalProposalManifest,
)
from parity.stages._common import build_metadata
from parity.stages.stage1 import run_stage1
from parity.stages.stage2 import run_stage2
from parity.stages.stage3 import run_stage3


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return _load_json(path)
    except Exception:
        return None


def _stage_metadata_path(directory: Path, stage: int) -> Path:
    return directory / f"stage{stage}.metadata.json"


def _write_stage_metadata(output_path: Path, stage: int, payload: dict[str, Any]) -> None:
    _write_json(output_path.parent / "metadata.json", payload)
    _write_json(_stage_metadata_path(output_path.parent, stage), payload)


def _resolve_budget_policy(
    config: ParityConfig,
    base: ResolvedSpendCaps,
) -> Literal["static", "carryforward"]:
    policy = config.spend.budget_policy
    if policy == "auto":
        return "static" if base.source == "explicit_stage_overrides" else "carryforward"
    return policy


def _resolved_spend_payload(resolved: ResolvedSpendCaps) -> dict[str, Any]:
    return {
        "analysis_total_spend_cap_usd": resolved.analysis_total_spend_cap_usd,
        "stage1_agent_cap_usd": resolved.stage1_agent_cap_usd,
        "stage2_agent_cap_usd": resolved.stage2_agent_cap_usd,
        "stage2_embedding_cap_usd": resolved.stage2_embedding_cap_usd,
        "stage3_agent_cap_usd": resolved.stage3_agent_cap_usd,
        "source": resolved.source,
    }


def _coerce_cost(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    return None


def _extract_stage2_embedding_spend(metadata: dict[str, Any] | None) -> float | None:
    if not isinstance(metadata, dict):
        return None
    embedding = metadata.get("embedding")
    if not isinstance(embedding, dict):
        return 0.0
    value = _coerce_cost(embedding.get("estimated_cost_usd"))
    return value if value is not None else 0.0


def _load_prior_stage_metadata(
    *,
    stage: int,
    manifest_path: Path | None,
    analysis_path: Path | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    stage1_metadata: dict[str, Any] | None = None
    stage2_metadata: dict[str, Any] | None = None

    if stage >= 2 and manifest_path is not None:
        stage1_metadata = _load_optional_json(_stage_metadata_path(manifest_path.parent, 1))
        if stage1_metadata is None and stage == 2:
            stage1_metadata = _load_optional_json(manifest_path.parent / "metadata.json")

    if stage >= 3 and analysis_path is not None:
        stage2_metadata = _load_optional_json(_stage_metadata_path(analysis_path.parent, 2))
        if stage2_metadata is None:
            stage2_metadata = _load_optional_json(analysis_path.parent / "metadata.json")

    return stage1_metadata, stage2_metadata


def _build_effective_spend_caps(
    *,
    stage: int,
    config: ParityConfig,
    stage1_metadata: dict[str, Any] | None = None,
    stage2_metadata: dict[str, Any] | None = None,
) -> tuple[ResolvedSpendCaps, dict[str, Any]]:
    base = config.resolve_spend_caps()
    applied_policy = _resolve_budget_policy(config, base)
    effective = base
    previous_spend_usd = 0.0
    future_reserve_usd = 0.0
    budget_adjustment_usd = 0.0
    metadata_complete = True
    policy_reason = "Using planned spend caps."

    if stage == 2:
        future_reserve_usd = base.stage2_embedding_cap_usd + base.stage3_agent_cap_usd
    elif stage == 3:
        future_reserve_usd = 0.0

    if applied_policy == "carryforward" and stage > 1:
        stage1_cost = _coerce_cost(stage1_metadata.get("cost_usd")) if isinstance(stage1_metadata, dict) else None
        if stage1_cost is None:
            applied_policy = "static"
            metadata_complete = False
            policy_reason = "Carryforward requested but Stage 1 metadata was unavailable; using planned spend caps."
        elif stage == 2:
            previous_spend_usd = stage1_cost
            stage2_agent_cap_usd = max(
                base.analysis_total_spend_cap_usd
                - previous_spend_usd
                - base.stage2_embedding_cap_usd
                - base.stage3_agent_cap_usd,
                0.0,
            )
            effective = ResolvedSpendCaps(
                analysis_total_spend_cap_usd=base.analysis_total_spend_cap_usd,
                stage1_agent_cap_usd=base.stage1_agent_cap_usd,
                stage2_agent_cap_usd=stage2_agent_cap_usd,
                stage2_embedding_cap_usd=base.stage2_embedding_cap_usd,
                stage3_agent_cap_usd=base.stage3_agent_cap_usd,
                source=base.source,
            )
            budget_adjustment_usd = stage2_agent_cap_usd - base.stage2_agent_cap_usd
            policy_reason = "Applied carryforward from unused Stage 1 budget while preserving Stage 2 embedding and Stage 3 baseline reserves."
        elif stage == 3:
            stage2_agent_cost = _coerce_cost(stage2_metadata.get("cost_usd")) if isinstance(stage2_metadata, dict) else None
            stage2_embedding_cost = _extract_stage2_embedding_spend(stage2_metadata)
            if stage2_agent_cost is None or stage2_embedding_cost is None:
                applied_policy = "static"
                metadata_complete = False
                policy_reason = (
                    "Carryforward requested but Stage 2 metadata was unavailable or incomplete; "
                    "using planned spend caps."
                )
            else:
                previous_spend_usd = stage1_cost + stage2_agent_cost + stage2_embedding_cost
                stage3_agent_cap_usd = max(base.analysis_total_spend_cap_usd - previous_spend_usd, 0.0)
                effective = ResolvedSpendCaps(
                    analysis_total_spend_cap_usd=base.analysis_total_spend_cap_usd,
                    stage1_agent_cap_usd=base.stage1_agent_cap_usd,
                    stage2_agent_cap_usd=base.stage2_agent_cap_usd,
                    stage2_embedding_cap_usd=base.stage2_embedding_cap_usd,
                    stage3_agent_cap_usd=stage3_agent_cap_usd,
                    source=base.source,
                )
                budget_adjustment_usd = stage3_agent_cap_usd - base.stage3_agent_cap_usd
                policy_reason = "Applied carryforward from unused Stage 1 and Stage 2 budget into the terminal Stage 3 budget."

    return effective, {
        "budget_policy_configured": config.spend.budget_policy,
        "budget_policy_applied": applied_policy,
        "budget_policy_reason": policy_reason,
        "budget_metadata_complete": metadata_complete,
        "budget_previous_spend_usd": previous_spend_usd,
        "budget_future_reserve_usd": future_reserve_usd,
        "budget_adjustment_usd": budget_adjustment_usd,
        "planned_spend_caps": _resolved_spend_payload(base),
        "effective_spend_caps": _resolved_spend_payload(effective),
    }


def _build_budget_failure_metadata(
    stage: int,
    exc: BudgetExceededError,
    extra: dict[str, Any] | None = None,
) -> dict:
    metadata = {
        "stage": stage,
        "status": "budget_exceeded",
        "cost_usd": exc.cost_usd,
        "error": exc.message,
    }
    if exc.details:
        metadata.update(exc.details)
    if extra:
        metadata.update(extra)
    return metadata


@click.command("run-stage")
@click.argument("stage", type=click.IntRange(1, 3))
@click.option("--pr-number", type=int)
@click.option("--base-branch")
@click.option("--manifest", "manifest_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--analysis", "analysis_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--config", "config_path", default="parity.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def run_stage_command(
    stage: int,
    pr_number: int | None,
    base_branch: str | None,
    manifest_path: Path | None,
    analysis_path: Path | None,
    output_path: Path,
    config_path: Path,
) -> None:
    try:
        config = ParityConfig.load(config_path, allow_missing=True)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(5) from exc
    for warning in config.compatibility_warnings():
        click.echo(f"parity: warning: {warning}", err=True)

    try:
        ParityConfig.load(config_path, allow_missing=False)
        click.echo(f"[parity] Config loaded from {config_path}", err=True)
    except ConfigError:
        click.echo(f"[parity] Config not found at {config_path} — using defaults", err=True)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            click.echo(
                "parity: warning: parity.yaml not found; running with empty artifact detection.\n"
                "No behavioral changes will be detected until you run `parity init` and commit the result.",
                err=True,
            )

    context = load_context_pack(config, repo_root=Path.cwd())
    stage1_metadata, stage2_metadata = _load_prior_stage_metadata(
        stage=stage,
        manifest_path=manifest_path,
        analysis_path=analysis_path,
    )
    effective_spend, budget_metadata = _build_effective_spend_caps(
        stage=stage,
        config=config,
        stage1_metadata=stage1_metadata,
        stage2_metadata=stage2_metadata,
    )
    try:
        if stage == 1:
            if pr_number is None or base_branch is None:
                raise SystemExit(5)
            click.echo(f"[parity] Stage 1 starting — PR #{pr_number}, base={base_branch}", err=True)
            raw_change_data = build_raw_change_data(
                base_branch,
                pr_number,
                config_path,
                allow_missing_config=True,
            )
            result = run_stage1(raw_change_data.model_dump(mode="json"), context, config, resolved_spend=effective_spend)
            metadata = build_metadata(1, result, extra=budget_metadata)
            cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a"
            click.echo(
                f"[parity] Stage 1 complete — model={result.model} cost={cost} "
                f"duration={result.duration_ms}ms turns={result.num_turns}",
                err=True,
            )
        elif stage == 2:
            if manifest_path is None:
                raise SystemExit(5)
            manifest = _load_json(manifest_path)
            change_count = len(manifest.get("changes", []))
            click.echo(f"[parity] Stage 2 starting — {change_count} change(s) from Stage 1", err=True)
            result = run_stage2(manifest, config, resolved_spend=effective_spend)
            metadata = build_metadata(2, result, extra=budget_metadata)
            cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a"
            click.echo(
                f"[parity] Stage 2 complete — model={result.model} cost={cost} "
                f"duration={result.duration_ms}ms turns={result.num_turns} gaps={len(result.data.gaps)}",
                err=True,
            )
        else:
            if manifest_path is None or analysis_path is None:
                raise SystemExit(5)
            manifest = _load_json(manifest_path)
            analysis = _load_json(analysis_path)
            target_count = len(analysis.get("resolved_targets", []))
            click.echo(f"[parity] Stage 3 starting — {target_count} resolved target(s) from Stage 2", err=True)
            result = run_stage3(manifest, analysis, context, config, resolved_spend=effective_spend)
            metadata = build_metadata(3, result, extra=budget_metadata)
            cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/a"
            click.echo(
                f"[parity] Stage 3 complete — model={result.model} cost={cost} "
                f"duration={result.duration_ms}ms intents={result.data.intent_count}",
                err=True,
            )
            commit_sha = manifest.get("commit_sha", "unknown")
            run_dir = output_path.parent / "runs" / commit_sha
            try:
                write_run_artifacts(
                    run_dir=run_dir,
                    stage1_manifest=BehaviorChangeManifest.model_validate(manifest),
                    stage2_manifest=EvalAnalysisManifest.model_validate(analysis),
                    proposal=result.data,
                    metadata=metadata,
                )
            except Exception as exc:
                click.echo(f"[parity] warning: run artifact export failed: {exc}", err=True)
    except BudgetExceededError as exc:
        if exc.partial_result is not None:
            _write_json(output_path, exc.partial_result)
        _write_stage_metadata(
            output_path,
            stage,
            _build_budget_failure_metadata(stage, exc, extra=budget_metadata),
        )
        click.echo(str(exc), err=True)
        raise SystemExit(3) from exc
    except SchemaValidationError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(2) from exc
    except GitDiffError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(4) from exc
    except StageError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    _write_json(output_path, result.data.model_dump(mode="json"))
    _write_stage_metadata(output_path, stage, metadata)


if __name__ == "__main__":
    run_stage_command()
