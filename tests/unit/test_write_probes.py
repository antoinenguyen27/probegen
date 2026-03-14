from __future__ import annotations

import json
from pathlib import Path

from probegen.cli.write_probes import write_probes_from_proposal
from probegen.config import MappingConfig, PlatformsConfig, ProbegenConfig, PromptfooPlatformConfig
from probegen.models import ProbeProposal


def _load_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_stage_artifacts(tmp_path: Path) -> Path:
    proposal_path = tmp_path / "stage3.json"
    proposal_path.write_text(Path("tests/fixtures/sample_proposal.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "stage2.json").write_text(Path("tests/fixtures/sample_gaps.json").read_text(encoding="utf-8"), encoding="utf-8")
    return proposal_path


def test_write_probes_uses_promptfoo_mapping_and_counts_success(tmp_path: Path) -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))
    proposal_path = _write_stage_artifacts(tmp_path)
    config = ProbegenConfig(
        platforms=PlatformsConfig(promptfoo=PromptfooPlatformConfig(config_path=str(tmp_path / "promptfooconfig.yaml"))),
        mappings=[
            MappingConfig(
                artifact="prompts/citation_agent/system_prompt.md",
                platform="promptfoo",
                dataset=str(tmp_path / "promptfooconfig.yaml"),
            )
        ],
    )

    outcome = write_probes_from_proposal(proposal, config=config, proposal_path=proposal_path)

    assert outcome.exit_code == 0
    assert outcome.total_written == 2
    assert outcome.written_targets == [f"promptfoo:{tmp_path / 'promptfooconfig.yaml'}"]
    assert (tmp_path / "promptfooconfig.yaml").exists()


def test_write_probes_fails_when_no_mapping_or_fallback_exists(tmp_path: Path) -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("tests/fixtures/sample_proposal.json"))
    proposal_path = _write_stage_artifacts(tmp_path)

    outcome = write_probes_from_proposal(proposal, config=ProbegenConfig(), proposal_path=proposal_path)

    assert outcome.exit_code == 2
    assert outcome.total_written == 0
    assert outcome.written_targets == []
    assert outcome.failures == [
        "No write target found for gap gap_001 (prompts/citation_agent/system_prompt.md)",
    ]
