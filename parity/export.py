from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from parity import __version__
from parity.integrations.promptfoo import PromptfooWriter
from parity.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal
from parity.models.eval_case import ConversationMessage


def create_run_artifact_dir(commit_sha: str, base_dir: str | Path = ".parity/runs") -> Path:
    run_dir = Path(base_dir) / commit_sha
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def render_summary_markdown(proposal: ProbeProposal) -> str:
    lines = ["# Parity Probe Summary", ""]
    for probe in proposal.probes:
        lines.extend(
            [
                f"## {probe.probe_id} ({probe.probe_type})",
                f"- Gap: {probe.gap_id}",
                f"- Expected behavior: {probe.expected_behavior}",
                f"- Rationale: {probe.probe_rationale}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def export_promptfoo_yaml(
    proposal: ProbeProposal,
    *,
    output_path: str | Path,
    artifact_path: str | None = None,
) -> dict[str, Path]:
    writer = PromptfooWriter()
    return writer.write_tests(
        proposal.probes,
        test_file=output_path,
        artifact_path=artifact_path,
        pr_number=proposal.pr_number,
        version=__version__,
        commit_sha=proposal.commit_sha,
    )


def export_deepeval_stub(proposal: ProbeProposal, *, output_path: str | Path) -> Path:
    path = Path(output_path)
    lines = [
        '"""Parity DeepEval stub export."""',
        "",
        "from deepeval.test_case import LLMTestCase",
        "",
        "CASES = [",
    ]
    for probe in proposal.probes:
        probe_input = (
            [item.model_dump() if isinstance(item, ConversationMessage) else item for item in probe.input]
            if isinstance(probe.input, list)
            else probe.input
        )
        lines.append(
            "    LLMTestCase("
            f"input={json.dumps(probe_input, ensure_ascii=True)}, "
            f"expected_output={json.dumps(probe.expected_behavior, ensure_ascii=True)}"
            "),"
        )
    lines.extend(["]", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_run_artifacts(
    *,
    run_dir: str | Path,
    stage1_manifest: BehaviorChangeManifest | None = None,
    stage2_manifest: CoverageGapManifest | None = None,
    proposal: ProbeProposal | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Path]:
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    if stage1_manifest is not None:
        path = directory / "BehaviorChangeManifest.json"
        path.write_text(stage1_manifest.model_dump_json(indent=2), encoding="utf-8")
        outputs["stage1"] = path
    if stage2_manifest is not None:
        path = directory / "CoverageGapManifest.json"
        path.write_text(stage2_manifest.model_dump_json(indent=2), encoding="utf-8")
        outputs["stage2"] = path
    if proposal is not None:
        raw_path = directory / "ProbeProposal.json"
        raw_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
        outputs["proposal"] = raw_path
        promptfoo_outputs = export_promptfoo_yaml(proposal, output_path=directory / "probes.yaml")
        outputs.update(promptfoo_outputs)
        summary_path = directory / "summary.md"
        summary_path.write_text(render_summary_markdown(proposal), encoding="utf-8")
        outputs["summary"] = summary_path
        deepeval_path = export_deepeval_stub(proposal, output_path=directory / "probes_deepeval.py")
        outputs["deepeval"] = deepeval_path
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(metadata or {}, indent=2), encoding="utf-8")
    outputs["metadata"] = metadata_path
    return outputs
