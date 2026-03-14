from __future__ import annotations

import json
from pathlib import Path

import click

from probegen.cli.get_behavior_diff import build_raw_change_data
from probegen.cli.setup_mcp import generate_mcp_config
from probegen.config import ProbegenConfig
from probegen.context import count_tokens, load_context_pack
from probegen.errors import BudgetExceededError, ConfigError, GitDiffError, SchemaValidationError, StageError
from probegen.stages._common import build_metadata
from probegen.stages.stage1 import run_stage1
from probegen.stages.stage2 import run_stage2
from probegen.stages.stage3 import run_stage3


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@click.command("run-stage")
@click.argument("stage", type=click.IntRange(1, 3))
@click.option("--pr-number", type=int)
@click.option("--base-branch")
@click.option("--manifest", "manifest_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--gaps", "gaps_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--config", "config_path", default="probegen.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def run_stage_command(
    stage: int,
    pr_number: int | None,
    base_branch: str | None,
    manifest_path: Path | None,
    gaps_path: Path | None,
    output_path: Path,
    config_path: Path,
) -> None:
    try:
        config = ProbegenConfig.load(config_path, allow_missing=True)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(5) from exc

    context = load_context_pack(config, repo_root=Path.cwd())
    mcp_payload = generate_mcp_config(config, dict(__import__("os").environ))
    mcp_path = Path(".claude/mcp_servers.json")
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(mcp_payload, indent=2), encoding="utf-8")

    try:
        if stage == 1:
            if pr_number is None or base_branch is None:
                raise SystemExit(5)
            raw_change_data = build_raw_change_data(
                base_branch,
                pr_number,
                config_path,
                allow_missing_config=True,
            )
            result = run_stage1(raw_change_data.model_dump(mode="json"), context, config)
            metadata = build_metadata(1, result)
        elif stage == 2:
            if manifest_path is None:
                raise SystemExit(5)
            manifest = _load_json(manifest_path)
            result = run_stage2(manifest, config, mcp_servers=mcp_path if mcp_payload["mcpServers"] else {})
            metadata = build_metadata(2, result)
        else:
            if manifest_path is None or gaps_path is None:
                raise SystemExit(5)
            manifest = _load_json(manifest_path)
            gaps = _load_json(gaps_path)
            result = run_stage3(manifest, gaps, context, config)
            metadata = build_metadata(3, result)
    except BudgetExceededError as exc:
        if exc.partial_result is not None:
            _write_json(output_path, exc.partial_result)
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
    _write_json(output_path.parent / "metadata.json", metadata)


if __name__ == "__main__":
    run_stage_command()
