from __future__ import annotations

import json
from pathlib import Path

import pytest

from probegen.models import BehaviorChangeManifest, CoverageGapManifest, ProbeProposal

_FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture by filename from tests/fixtures/."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def sample_manifest() -> BehaviorChangeManifest:
    return BehaviorChangeManifest.model_validate(load_fixture("sample_manifest.json"))


@pytest.fixture()
def sample_gaps() -> CoverageGapManifest:
    return CoverageGapManifest.model_validate(load_fixture("sample_gaps.json"))


@pytest.fixture()
def sample_proposal() -> ProbeProposal:
    return ProbeProposal.model_validate(load_fixture("sample_proposal.json"))
