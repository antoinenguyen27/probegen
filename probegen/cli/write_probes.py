from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import click

from probegen.config import MappingConfig, ProbegenConfig
from probegen.errors import GithubApiError, PlatformIntegrationError
from probegen.github import post_pr_comment, render_results_comment
from probegen.integrations.braintrust import BraintrustWriter
from probegen.integrations.langsmith import LangSmithWriter
from probegen.integrations.phoenix import PhoenixWriter
from probegen.integrations.promptfoo import PromptfooWriter
from probegen.models import CoverageGapManifest, ProbeCase, ProbeProposal


def _load_optional_stage2(proposal_path: Path) -> CoverageGapManifest | None:
    for candidate in ("stage2.json", "CoverageGapManifest.json"):
        path = proposal_path.parent / candidate
        if path.exists():
            return CoverageGapManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return None


def _selected_probes(proposal: ProbeProposal) -> list[ProbeCase]:
    approved = [probe for probe in proposal.probes if probe.approved]
    return approved or proposal.probes


def _group_probes(
    proposal: ProbeProposal,
    stage2_manifest: CoverageGapManifest | None,
    config: ProbegenConfig,
) -> tuple[dict[tuple[str, str | None, str | None], list[ProbeCase]], list[str]]:
    grouped: dict[tuple[str, str | None, str | None], list[ProbeCase]] = defaultdict(list)
    warnings: list[str] = []
    gap_lookup = {gap.gap_id: gap for gap in (stage2_manifest.gaps if stage2_manifest else [])}

    for probe in _selected_probes(proposal):
        gap = gap_lookup.get(probe.gap_id)
        mapping = config.find_mapping(gap.artifact_path) if gap else None
        if mapping is None:
            if config.platforms.promptfoo:
                grouped[("promptfoo", config.platforms.promptfoo.config_path, None)].append(probe)
            else:
                warnings.append(f"No mapping found for gap {probe.gap_id}")
            continue
        grouped[(mapping.platform, mapping.dataset or config.platforms.promptfoo.config_path if config.platforms.promptfoo else None, mapping.project)].append(probe)
    return grouped, warnings


def write_probes_from_proposal(
    proposal: ProbeProposal,
    *,
    config: ProbegenConfig,
    proposal_path: Path,
) -> tuple[int, list[str]]:
    stage2_manifest = _load_optional_stage2(proposal_path)
    grouped, warnings = _group_probes(proposal, stage2_manifest, config)
    failures: list[str] = []

    for (platform, target, project), probes in grouped.items():
        try:
            if platform == "langsmith":
                LangSmithWriter(api_key=os.environ.get("LANGSMITH_API_KEY")).create_examples(
                    probes,
                    dataset_name=target,
                    source_pr=proposal.pr_number,
                    source_commit=proposal.commit_sha,
                )
            elif platform == "braintrust":
                BraintrustWriter(
                    api_key=os.environ.get("BRAINTRUST_API_KEY"),
                    org_name=config.platforms.braintrust.org if config.platforms.braintrust else None,
                ).create_examples(probes, project=project or "", dataset_name=target or "")
            elif platform == "arize_phoenix":
                PhoenixWriter(
                    base_url=config.platforms.arize_phoenix.base_url if config.platforms.arize_phoenix else None,
                    api_key=os.environ.get("PHOENIX_API_KEY"),
                ).create_examples(probes, dataset_name=target or "")
            elif platform == "promptfoo":
                PromptfooWriter().write_tests(
                    probes,
                    test_file=target or "promptfooconfig.yaml",
                    pr_number=proposal.pr_number,
                    commit_sha=proposal.commit_sha,
                )
        except Exception as exc:
            failures.append(f"{platform}:{target}: {exc}")

    if failures and len(failures) == len(grouped):
        return 2, warnings + failures
    if failures:
        return 1, warnings + failures
    return 0, warnings


@click.command("write-probes")
@click.option("--proposal", "proposal_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--config", "config_path", default="probegen.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def write_probes_command(proposal_path: Path, config_path: Path) -> None:
    proposal = ProbeProposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
    config = ProbegenConfig.load(config_path, allow_missing=True)
    exit_code, messages = write_probes_from_proposal(proposal, config=config, proposal_path=proposal_path)

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    pr_number = os.environ.get("PR_NUMBER")
    if repo and token and pr_number:
        try:
            body = render_results_comment(
                dataset_name=", ".join({message.split(":")[0] for message in messages}) or "configured datasets",
                total_written=len(_selected_probes(proposal)),
                failures=[
                    {
                        "probe_id": "n/a",
                        "probe_type": "n/a",
                        "failure": message,
                    }
                    for message in messages
                ]
                if messages
                else None,
            )
            post_pr_comment(int(pr_number), body, repo, token)
        except GithubApiError:
            pass

    for message in messages:
        click.echo(message, err=True)
    raise SystemExit(exit_code)


def main() -> None:
    write_probes_command()


if __name__ == "__main__":
    main()
