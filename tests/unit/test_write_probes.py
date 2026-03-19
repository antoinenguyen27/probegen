from __future__ import annotations

import json
from pathlib import Path

from probegen.cli.write_probes import _selected_probes, write_probes_from_proposal
from probegen.config import MappingConfig, PlatformsConfig, ProbegenConfig, PromptfooPlatformConfig
from probegen.models import ProbeProposal

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _write_stage_artifacts(tmp_path: Path) -> Path:
    proposal_path = tmp_path / "stage3.json"
    proposal_path.write_text(
        (_FIXTURES / "sample_proposal.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "stage2.json").write_text(
        (_FIXTURES / "sample_gaps.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return proposal_path


def test_write_probes_uses_promptfoo_mapping_and_counts_success(tmp_path: Path) -> None:
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))
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
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))
    proposal_path = _write_stage_artifacts(tmp_path)

    outcome = write_probes_from_proposal(proposal, config=ProbegenConfig(), proposal_path=proposal_path)

    assert outcome.exit_code == 2
    assert outcome.total_written == 0
    assert outcome.written_targets == []
    assert outcome.failures == [
        "No write target found for gap gap_001 (prompts/citation_agent/system_prompt.md)",
    ]


def test_write_probes_falls_back_to_platform_promptfoo_when_no_explicit_mapping(tmp_path: Path) -> None:
    """When no artifact mapping exists but platforms.promptfoo is configured, probes go to that file."""
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))
    proposal_path = _write_stage_artifacts(tmp_path)
    fallback_config_path = tmp_path / "promptfooconfig.yaml"
    config = ProbegenConfig(
        platforms=PlatformsConfig(
            promptfoo=PromptfooPlatformConfig(config_path=str(fallback_config_path))
        ),
        mappings=[],  # no artifact-specific mappings
    )

    outcome = write_probes_from_proposal(proposal, config=config, proposal_path=proposal_path)

    assert outcome.exit_code == 0
    assert outcome.total_written == 2
    assert fallback_config_path.exists()


def test_selected_probes_returns_all_when_none_approved() -> None:
    """When no probe has approved=True, the full list is returned."""
    proposal = ProbeProposal.model_validate(_load_fixture("sample_proposal.json"))

    # All probes in sample fixture have approved=False
    result = _selected_probes(proposal)

    assert result == proposal.probes


def test_selected_probes_returns_only_approved_subset() -> None:
    """When at least one probe is approved, only those probes are returned."""
    from datetime import datetime, timezone

    fixture = _load_fixture("sample_proposal.json")
    # Mark only the first probe as approved
    fixture["probes"][0]["approved"] = True
    fixture["probes"][1]["approved"] = False
    proposal = ProbeProposal.model_validate(fixture)

    result = _selected_probes(proposal)

    assert len(result) == 1
    assert result[0].probe_id == fixture["probes"][0]["probe_id"]
