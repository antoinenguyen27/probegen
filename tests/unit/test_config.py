from __future__ import annotations

from pathlib import Path

import pytest

from parity.config import ParityConfig
from parity.errors import ConfigError


def test_config_loads_full_reference(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text(
        """
version: 2
behavior_artifacts:
  paths:
    - "prompts/**"
  python_patterns:
    - "*_prompt"
  exclude:
    - "tests/**"
guardrail_artifacts:
  paths:
    - "judges/**"
  python_patterns:
    - "*_judge*"
context:
  product: "context/product.md"
  users: "context/users.md"
  interactions: "context/interactions.md"
  good_examples: "context/good_examples.md"
  bad_examples: "context/bad_examples.md"
  traces_dir: "context/traces/"
  trace_max_samples: 7
platforms:
  langsmith:
    api_key_env: LANGSMITH_API_KEY
  braintrust:
    api_key_env: BRAINTRUST_API_KEY
    org: my-org
  arize_phoenix:
    api_key_env: PHOENIX_API_KEY
    base_url: https://app.phoenix.arize.com
  promptfoo:
    config_path: promptfooconfig.yaml
evals:
  discovery:
    repo_asset_globs:
      - "evals/*.yaml"
    platform_discovery_order:
      - langsmith
      - promptfoo
    sample_limit_per_target: 15
    allow_repo_asset_discovery: true
  rules:
    - artifact: "prompts/**"
      preferred_platform: langsmith
      preferred_target: citation-agent-evals
      allowed_methods:
        - judge
        - hybrid
      preferred_methods:
        - hybrid
  write:
    require_native_rendering: true
    min_render_confidence: 0.8
    create_missing_targets: false
    allow_review_only_exports: true
  evaluators:
    formal_discovery_required: false
    allow_inference_fallback: true
    require_binding_verification: false
    min_binding_confidence: 0.9
embedding:
  model: text-embedding-3-small
  cache_path: .parity/embedding_cache.db
  dimensions: 256
similarity:
  duplicate_threshold: 0.9
  boundary_threshold: 0.7
generation:
  proposal_limit: 8
  candidate_intent_pool_limit: 20
  diversity_limit_per_gap: 2
approval:
  label: parity:approve
auto_run:
  enabled: true
  fail_on: regression_guard
  notify: pr_comment
spend:
  analysis_total_spend_cap_usd: 3.0
""".strip(),
        encoding="utf-8",
    )

    config = ParityConfig.load(config_path)
    resolved_spend = config.resolve_spend_caps()

    assert config.context.trace_max_samples == 7
    assert config.embedding.dimensions == 256
    assert config.generation.proposal_limit == 8
    assert config.generation.resolve_candidate_intent_pool_limit() == 20
    assert config.find_eval_rule("prompts/foo/bar.md") is not None
    assert config.evals.discovery.sample_limit_per_target == 15
    assert config.evals.evaluators.min_binding_confidence == pytest.approx(0.9)
    assert resolved_spend.analysis_total_spend_cap_usd == pytest.approx(3.0)
    assert resolved_spend.stage1_agent_cap_usd == pytest.approx(0.9)
    assert resolved_spend.stage2_agent_cap_usd == pytest.approx(0.54)
    assert resolved_spend.stage2_embedding_cap_usd == pytest.approx(0.36)
    assert resolved_spend.stage3_agent_cap_usd == pytest.approx(1.2)


def test_config_loads_defaults_when_missing_allowed() -> None:
    config = ParityConfig.load("missing.yaml", allow_missing=True)
    assert config.embedding.model == "text-embedding-3-small"
    assert config.resolve_spend_caps().analysis_total_spend_cap_usd == pytest.approx(2.50)


def test_config_missing_raises_by_default() -> None:
    with pytest.raises(ConfigError):
        ParityConfig.load("missing.yaml")


def test_config_invalid_threshold_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text("similarity:\n  duplicate_threshold: 1.5\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        ParityConfig.load(config_path)


def test_generation_candidate_intent_pool_limit_auto_derives() -> None:
    config = ParityConfig.model_validate({"generation": {"proposal_limit": 9}})
    assert config.generation.resolve_candidate_intent_pool_limit() == 23


def test_stage_spend_overrides_must_be_complete(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text(
        """
spend:
  stage1_agent_cap_usd: 1.0
  stage2_agent_cap_usd: 0.5
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        ParityConfig.load(config_path)


def test_config_reports_deprecated_workflow_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text(
        """
version: 2
approval:
  label: custom:approve
auto_run:
  enabled: false
""".strip(),
        encoding="utf-8",
    )

    config = ParityConfig.load(config_path)

    assert config.compatibility_warnings() == [
        "`approval` is deprecated and ignored. Parity always uses the fixed GitHub label `parity:approve`.",
        "`auto_run` is deprecated and ignored. Edit `.github/workflows/parity.yml` directly to change workflow policy.",
    ]


def test_config_without_legacy_workflow_sections_has_no_compatibility_warnings() -> None:
    assert ParityConfig.model_validate({}).compatibility_warnings() == []
