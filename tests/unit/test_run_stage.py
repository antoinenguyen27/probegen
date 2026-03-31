from __future__ import annotations

import pytest

from parity.cli.run_stage import _build_effective_spend_caps
from parity.config import ParityConfig


def test_build_effective_spend_caps_carries_forward_unused_stage1_budget_to_stage2() -> None:
    config = ParityConfig()

    effective, metadata = _build_effective_spend_caps(
        stage=2,
        config=config,
        stage1_metadata={"cost_usd": 0.2784},
    )

    assert effective.stage2_agent_cap_usd == pytest.approx(0.9216)
    assert effective.stage2_embedding_cap_usd == pytest.approx(0.30)
    assert metadata["budget_policy_applied"] == "carryforward"
    assert metadata["budget_adjustment_usd"] == pytest.approx(0.4716)


def test_build_effective_spend_caps_uses_remaining_total_for_stage3_including_stage2_embedding() -> None:
    config = ParityConfig()

    effective, metadata = _build_effective_spend_caps(
        stage=3,
        config=config,
        stage1_metadata={"cost_usd": 0.2784},
        stage2_metadata={
            "cost_usd": 0.4842,
            "embedding": {"estimated_cost_usd": 0.0001},
        },
    )

    assert effective.stage3_agent_cap_usd == pytest.approx(1.7373)
    assert metadata["budget_policy_applied"] == "carryforward"
    assert metadata["budget_previous_spend_usd"] == pytest.approx(0.7627)


def test_build_effective_spend_caps_falls_back_to_static_when_metadata_missing() -> None:
    config = ParityConfig()

    effective, metadata = _build_effective_spend_caps(stage=2, config=config, stage1_metadata=None)

    assert effective.stage2_agent_cap_usd == pytest.approx(config.resolve_spend_caps().stage2_agent_cap_usd)
    assert metadata["budget_policy_applied"] == "static"
    assert metadata["budget_metadata_complete"] is False


def test_build_effective_spend_caps_auto_policy_keeps_explicit_stage_overrides_static() -> None:
    config = ParityConfig.model_validate(
        {
            "spend": {
                "stage1_agent_cap_usd": 0.8,
                "stage2_agent_cap_usd": 0.5,
                "stage2_embedding_cap_usd": 0.2,
                "stage3_agent_cap_usd": 1.0,
            }
        }
    )

    effective, metadata = _build_effective_spend_caps(
        stage=2,
        config=config,
        stage1_metadata={"cost_usd": 0.1},
    )

    assert effective.stage2_agent_cap_usd == pytest.approx(0.5)
    assert metadata["budget_policy_applied"] == "static"
