from __future__ import annotations

from parity.cli.init_cmd import render_workflow_template
from parity.config import (
    FIXED_APPROVAL_LABEL,
    ArizePhoenixPlatformConfig,
    BraintrustPlatformConfig,
    LangSmithPlatformConfig,
    ParityConfig,
    PlatformsConfig,
)


def test_render_workflow_template_limits_writeback_secrets_to_configured_platforms() -> None:
    workflow = render_workflow_template(
        ParityConfig(
            platforms=PlatformsConfig(
                langsmith=LangSmithPlatformConfig(),
                braintrust=None,
                arize_phoenix=None,
            )
        )
    )

    assert "LANGSMITH_API_KEY" in workflow
    assert "BRAINTRUST_API_KEY" not in workflow[workflow.index("Write evals to platform") :]
    assert "PHOENIX_API_KEY" not in workflow[workflow.index("Write evals to platform") :]
    assert "post-write-comment" in workflow
    assert "--skip-comment" in workflow
    assert "actions/setup-node@v4" in workflow
    assert f"contains(github.event.pull_request.labels.*.name, '{FIXED_APPROVAL_LABEL}')" in workflow

    analysis_section = workflow[workflow.index("Stage 2 — Eval Analysis") : workflow.index("Stage 3 — Native Eval Synthesis")]
    assert "LANGSMITH_API_KEY" in analysis_section
    assert "BRAINTRUST_API_KEY" not in analysis_section
    assert "PHOENIX_API_KEY" not in analysis_section


def test_render_workflow_template_includes_multiple_platform_keys_when_configured() -> None:
    workflow = render_workflow_template(
        ParityConfig(
            platforms=PlatformsConfig(
                langsmith=LangSmithPlatformConfig(),
                braintrust=BraintrustPlatformConfig(),
                arize_phoenix=ArizePhoenixPlatformConfig(),
            )
        )
    )

    write_section = workflow[workflow.index("Write evals to platform") :]
    analysis_section = workflow[workflow.index("Stage 2 — Eval Analysis") : workflow.index("Stage 3 — Native Eval Synthesis")]
    assert "LANGSMITH_API_KEY" in analysis_section
    assert "BRAINTRUST_API_KEY" in analysis_section
    assert "PHOENIX_API_KEY" in analysis_section
    assert "LANGSMITH_API_KEY" in write_section
    assert "BRAINTRUST_API_KEY" in write_section
    assert "PHOENIX_API_KEY" in write_section
