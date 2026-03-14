from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import click

from probegen.config import ProbegenConfig
from probegen.errors import GithubApiError
from probegen.github import post_pr_comment, render_results_comment
from probegen.integrations.braintrust import BraintrustWriter
from probegen.integrations.langsmith import LangSmithWriter
from probegen.integrations.phoenix import PhoenixWriter
from probegen.integrations.promptfoo import PromptfooWriter
from probegen.models import CoverageGapManifest, ProbeCase, ProbeProposal


@dataclass
class ProbeWriteOutcome:
    exit_code: int
    total_written: int = 0
    attempted_targets: list[str] = field(default_factory=list)
    written_targets: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def messages(self) -> list[str]:
        return self.failures


def _load_optional_stage2(proposal_path: Path) -> CoverageGapManifest | None:
    for candidate in ("stage2.json", "CoverageGapManifest.json"):
        path = proposal_path.parent / candidate
        if path.exists():
            return CoverageGapManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return None


def _selected_probes(proposal: ProbeProposal) -> list[ProbeCase]:
    approved = [probe for probe in proposal.probes if probe.approved]
    return approved or proposal.probes


def _target_label(platform: str, target: str | None, project: str | None) -> str:
    if platform == "braintrust":
        if project and target:
            return f"{platform}:{project}/{target}"
        return f"{platform}:{project or target or 'default'}"
    return f"{platform}:{target or project or 'default'}"


def _group_probes(
    proposal: ProbeProposal,
    stage2_manifest: CoverageGapManifest | None,
    config: ProbegenConfig,
) -> tuple[dict[tuple[str, str | None, str | None], list[ProbeCase]], list[str]]:
    grouped: dict[tuple[str, str | None, str | None], list[ProbeCase]] = defaultdict(list)
    failures: list[str] = []
    unresolved_gaps: set[tuple[str, str]] = set()
    gap_lookup = {gap.gap_id: gap for gap in (stage2_manifest.gaps if stage2_manifest else [])}

    for probe in _selected_probes(proposal):
        gap = gap_lookup.get(probe.gap_id)
        mapping = config.find_mapping(gap.artifact_path) if gap else None
        if mapping is None:
            if config.platforms.promptfoo:
                grouped[("promptfoo", config.platforms.promptfoo.config_path, None)].append(probe)
            else:
                artifact_path = gap.artifact_path if gap else "unknown artifact"
                unresolved_key = (probe.gap_id, artifact_path)
                if unresolved_key not in unresolved_gaps:
                    unresolved_gaps.add(unresolved_key)
                    failures.append(f"No write target found for gap {probe.gap_id} ({artifact_path})")
            continue
        target = mapping.dataset
        if mapping.platform == "promptfoo":
            target = mapping.dataset or (config.platforms.promptfoo.config_path if config.platforms.promptfoo else None)
        grouped[(mapping.platform, target, mapping.project)].append(probe)
    return grouped, failures


def write_probes_from_proposal(
    proposal: ProbeProposal,
    *,
    config: ProbegenConfig,
    proposal_path: Path,
) -> ProbeWriteOutcome:
    stage2_manifest = _load_optional_stage2(proposal_path)
    grouped, failures = _group_probes(proposal, stage2_manifest, config)
    attempted_targets = sorted({_target_label(platform, target, project) for platform, target, project in grouped})
    written_targets: list[str] = []
    total_written = 0

    for (platform, target, project), probes in grouped.items():
        label = _target_label(platform, target, project)
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
            total_written += len(probes)
            written_targets.append(label)
        except Exception as exc:
            failures.append(f"{label}: {exc}")

    if failures and total_written == 0:
        return ProbeWriteOutcome(
            exit_code=2,
            total_written=0,
            attempted_targets=attempted_targets,
            written_targets=[],
            failures=failures,
        )
    if failures:
        return ProbeWriteOutcome(
            exit_code=1,
            total_written=total_written,
            attempted_targets=attempted_targets,
            written_targets=written_targets,
            failures=failures,
        )
    return ProbeWriteOutcome(
        exit_code=0,
        total_written=total_written,
        attempted_targets=attempted_targets,
        written_targets=written_targets,
        failures=[],
    )


@click.command("write-probes")
@click.option("--proposal", "proposal_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--config", "config_path", default="probegen.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def write_probes_command(proposal_path: Path, config_path: Path) -> None:
    proposal = ProbeProposal.model_validate(json.loads(proposal_path.read_text(encoding="utf-8")))
    config = ProbegenConfig.load(config_path, allow_missing=True)
    outcome = write_probes_from_proposal(proposal, config=config, proposal_path=proposal_path)

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    pr_number = os.environ.get("PR_NUMBER")
    if repo and token and pr_number and (outcome.total_written > 0 or outcome.failures):
        try:
            body = render_results_comment(
                dataset_name=", ".join(outcome.written_targets or outcome.attempted_targets) or None,
                total_written=outcome.total_written,
                failures=[
                    {
                        "probe_id": "n/a",
                        "probe_type": "n/a",
                        "failure": message,
                    }
                    for message in outcome.failures
                ]
                if outcome.failures
                else None,
            )
            post_pr_comment(int(pr_number), body, repo, token)
        except GithubApiError:
            pass

    for message in outcome.messages:
        click.echo(message, err=True)
    raise SystemExit(outcome.exit_code)


def main() -> None:
    write_probes_command()


if __name__ == "__main__":
    main()
