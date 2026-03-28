from __future__ import annotations

from pathlib import Path

import pytest

from parity.config import ParityConfig
from parity.errors import ConfigError


def test_config_loads_full_reference(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text(
        """
version: 1
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
mappings:
  - artifact: "prompts/**"
    platform: langsmith
    dataset: citation-agent-evals
embedding:
  model: text-embedding-3-small
  cache_path: .parity/embedding_cache.db
  dimensions: 256
similarity:
  duplicate_threshold: 0.9
  boundary_threshold: 0.7
generation:
  max_probes_surfaced: 8
  max_probes_generated: 20
  diversity_limit_per_gap: 2
approval:
  label: parity:approve
auto_run:
  enabled: true
  fail_on: regression_guard
  notify: pr_comment
budgets:
  stage1_usd: 0.5
  stage2_usd: 0.75
  stage3_usd: 1.0
""".strip(),
        encoding="utf-8",
    )

    config = ParityConfig.load(config_path)

    assert config.context.trace_max_samples == 7
    assert config.embedding.dimensions == 256
    assert config.find_mapping("prompts/foo/bar.md") is not None


def test_config_loads_defaults_when_missing_allowed() -> None:
    config = ParityConfig.load("missing.yaml", allow_missing=True)
    assert config.embedding.model == "text-embedding-3-small"


def test_config_missing_raises_by_default() -> None:
    with pytest.raises(ConfigError):
        ParityConfig.load("missing.yaml")


def test_config_invalid_threshold_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text("similarity:\n  duplicate_threshold: 1.5\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        ParityConfig.load(config_path)
