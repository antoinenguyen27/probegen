from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, field_validator

from probegen.errors import ConfigError
from probegen.models._base import ProbegenModel

PlatformName = Literal["langsmith", "braintrust", "arize_phoenix", "promptfoo"]


class ArtifactDetectionConfig(ProbegenModel):
    paths: list[str] = Field(default_factory=list)
    python_patterns: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ContextConfig(ProbegenModel):
    product: str = "context/product.md"
    users: str = "context/users.md"
    interactions: str = "context/interactions.md"
    good_examples: str = "context/good_examples.md"
    bad_examples: str = "context/bad_examples.md"
    traces_dir: str = "context/traces/"
    trace_max_samples: int = 20

    @field_validator("trace_max_samples")
    @classmethod
    def validate_trace_max_samples(cls, value: int) -> int:
        if value < 0:
            raise ValueError("trace_max_samples must be non-negative")
        return value


class LangSmithPlatformConfig(ProbegenModel):
    api_key_env: str = "LANGSMITH_API_KEY"


class BraintrustPlatformConfig(ProbegenModel):
    api_key_env: str = "BRAINTRUST_API_KEY"
    org: str | None = None


class ArizePhoenixPlatformConfig(ProbegenModel):
    api_key_env: str = "PHOENIX_API_KEY"
    base_url: str = "https://app.phoenix.arize.com"


class PromptfooPlatformConfig(ProbegenModel):
    config_path: str = "promptfooconfig.yaml"


class PlatformsConfig(ProbegenModel):
    langsmith: LangSmithPlatformConfig | None = None
    braintrust: BraintrustPlatformConfig | None = None
    arize_phoenix: ArizePhoenixPlatformConfig | None = None
    promptfoo: PromptfooPlatformConfig | None = None


class MappingConfig(ProbegenModel):
    artifact: str
    platform: PlatformName
    dataset: str | None = None
    project: str | None = None
    eval_type: str | None = None

    def matches(self, artifact_path: str) -> bool:
        return fnmatch.fnmatch(artifact_path, self.artifact)


class EmbeddingConfig(ProbegenModel):
    model: str = "text-embedding-3-small"
    cache_path: str = ".probegen/embedding_cache.db"
    dimensions: int | None = None


class SimilarityConfig(ProbegenModel):
    duplicate_threshold: float = 0.88
    boundary_threshold: float = 0.72

    @field_validator("duplicate_threshold", "boundary_threshold")
    @classmethod
    def validate_thresholds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("similarity thresholds must be between 0 and 1")
        return value


class GenerationConfig(ProbegenModel):
    max_probes_surfaced: int = 8
    max_probes_generated: int = 20
    diversity_limit_per_gap: int = 2


class ApprovalConfig(ProbegenModel):
    label: str = "probegen:approve"


class AutoRunConfig(ProbegenModel):
    # Reserved for v2: auto_run config is parsed and stored but not yet executed.
    # The auto-run pipeline (platform-specific eval triggers + results post-back) is planned.
    enabled: bool = True
    fail_on: str | None = "regression_guard"
    notify: str | None = "pr_comment"


class BudgetsConfig(ProbegenModel):
    stage1_usd: float = 0.50
    stage2_usd: float = 0.75
    stage3_usd: float = 1.00


class ProbegenConfig(ProbegenModel):
    version: int = 1
    behavior_artifacts: ArtifactDetectionConfig = Field(default_factory=ArtifactDetectionConfig)
    guardrail_artifacts: ArtifactDetectionConfig = Field(default_factory=ArtifactDetectionConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    mappings: list[MappingConfig] = Field(default_factory=list)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    similarity: SimilarityConfig = Field(default_factory=SimilarityConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    auto_run: AutoRunConfig = Field(default_factory=AutoRunConfig)
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)

    @classmethod
    def load(cls, path: str | Path = "probegen.yaml", *, allow_missing: bool = False) -> Self:
        config_path = Path(path)
        if not config_path.exists():
            if allow_missing:
                return cls()
            raise ConfigError(f"Configuration file not found: {config_path}")

        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

        try:
            return cls.model_validate(payload)
        except Exception as exc:
            raise ConfigError(f"Invalid probegen configuration: {exc}") from exc

    def resolve_path(self, relative_path: str | Path, repo_root: str | Path | None = None) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            return path
        base = Path(repo_root) if repo_root is not None else Path.cwd()
        return base / path

    def find_mapping(self, artifact_path: str) -> MappingConfig | None:
        for mapping in self.mappings:
            if mapping.matches(artifact_path):
                return mapping
        return None
