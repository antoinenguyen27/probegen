from __future__ import annotations

import fnmatch
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, field_validator, model_validator

from parity.errors import ConfigError
from parity.models._base import ParityModel

PlatformName = Literal["langsmith", "braintrust", "arize_phoenix", "promptfoo"]
MethodKind = Literal[
    "deterministic",
    "judge",
    "hybrid",
    "pairwise",
    "human_review",
    "trajectory",
    "unknown",
]

DEFAULT_ANALYSIS_TOTAL_SPEND_CAP_USD = 2.50
DEFAULT_STAGE1_AGENT_SPEND_RATIO = 0.30
DEFAULT_STAGE2_AGENT_SPEND_RATIO = 0.18
DEFAULT_STAGE2_EMBEDDING_SPEND_RATIO = 0.12
DEFAULT_STAGE3_AGENT_SPEND_RATIO = 0.40

DEFAULT_REPO_ASSET_GLOBS = [
    "promptfooconfig.yaml",
    "promptfooconfig.yml",
    "**/promptfooconfig.yaml",
    "**/promptfooconfig.yml",
    "**/*promptfoo*.yaml",
    "**/*promptfoo*.yml",
    "**/eval*.yaml",
    "**/eval*.yml",
    "**/*eval*.py",
    "**/*eval*.ts",
    "**/*eval*.js",
    "**/*judge*.py",
    "**/*judge*.ts",
    "**/*judge*.js",
    "**/*scorer*.py",
    "**/*scorer*.ts",
    "**/*scorer*.js",
]


class ArtifactDetectionConfig(ParityModel):
    paths: list[str] = Field(default_factory=list)
    python_patterns: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ContextConfig(ParityModel):
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


class LangSmithPlatformConfig(ParityModel):
    api_key_env: str = "LANGSMITH_API_KEY"


class BraintrustPlatformConfig(ParityModel):
    api_key_env: str = "BRAINTRUST_API_KEY"
    org: str | None = None


class ArizePhoenixPlatformConfig(ParityModel):
    api_key_env: str = "PHOENIX_API_KEY"
    base_url: str = "https://app.phoenix.arize.com"


class PromptfooPlatformConfig(ParityModel):
    config_path: str = "promptfooconfig.yaml"


class PlatformsConfig(ParityModel):
    langsmith: LangSmithPlatformConfig | None = None
    braintrust: BraintrustPlatformConfig | None = None
    arize_phoenix: ArizePhoenixPlatformConfig | None = None
    promptfoo: PromptfooPlatformConfig | None = None


class EvalDiscoveryConfig(ParityModel):
    repo_asset_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_REPO_ASSET_GLOBS))
    platform_discovery_order: list[PlatformName] = Field(default_factory=list)
    sample_limit_per_target: int = 20
    allow_repo_asset_discovery: bool = True

    @field_validator("sample_limit_per_target")
    @classmethod
    def validate_sample_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sample_limit_per_target must be positive")
        return value


class EvalRuleConfig(ParityModel):
    artifact: str
    preferred_platform: PlatformName | None = None
    preferred_target: str | None = None
    preferred_project: str | None = None
    allowed_methods: list[MethodKind] = Field(default_factory=list)
    preferred_methods: list[MethodKind] = Field(default_factory=list)
    repo_asset_hints: list[str] = Field(default_factory=list)

    def matches(self, artifact_path: str) -> bool:
        return fnmatch.fnmatch(artifact_path, self.artifact)

    @model_validator(mode="after")
    def validate_method_preferences(self) -> "EvalRuleConfig":
        if self.allowed_methods and self.preferred_methods:
            disallowed = [method for method in self.preferred_methods if method not in self.allowed_methods]
            if disallowed:
                raise ValueError(
                    "preferred_methods must be a subset of allowed_methods when both are configured"
                )
        return self


class EvalWriteConfig(ParityModel):
    require_native_rendering: bool = True
    min_render_confidence: float = 0.70
    create_missing_targets: bool = False
    allow_review_only_exports: bool = True

    @field_validator("min_render_confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("min_render_confidence must be between 0 and 1")
        return value


class EvalEvaluatorConfig(ParityModel):
    formal_discovery_required: bool = False
    allow_inference_fallback: bool = True
    require_binding_verification: bool = False
    min_binding_confidence: float = 0.85

    @field_validator("min_binding_confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("min_binding_confidence must be between 0 and 1")
        return value


class EvalsConfig(ParityModel):
    discovery: EvalDiscoveryConfig = Field(default_factory=EvalDiscoveryConfig)
    rules: list[EvalRuleConfig] = Field(default_factory=list)
    write: EvalWriteConfig = Field(default_factory=EvalWriteConfig)
    evaluators: EvalEvaluatorConfig = Field(default_factory=EvalEvaluatorConfig)


class EmbeddingConfig(ParityModel):
    model: str = "text-embedding-3-small"
    cache_path: str = ".parity/embedding_cache.db"
    dimensions: int | None = None


class SimilarityConfig(ParityModel):
    duplicate_threshold: float = 0.88
    boundary_threshold: float = 0.72

    @field_validator("duplicate_threshold", "boundary_threshold")
    @classmethod
    def validate_thresholds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("similarity thresholds must be between 0 and 1")
        return value


class GenerationConfig(ParityModel):
    proposal_limit: int = 8
    candidate_intent_pool_limit: int | None = None
    diversity_limit_per_gap: int = 2

    @field_validator("proposal_limit", "diversity_limit_per_gap")
    @classmethod
    def validate_positive_ints(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("generation limits must be positive integers")
        return value

    @field_validator("candidate_intent_pool_limit")
    @classmethod
    def validate_candidate_intent_pool_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("candidate_intent_pool_limit must be positive when provided")
        return value

    @model_validator(mode="after")
    def validate_generation_relationships(self) -> "GenerationConfig":
        if self.candidate_intent_pool_limit is not None and self.candidate_intent_pool_limit < self.proposal_limit:
            raise ValueError(
                "candidate_intent_pool_limit must be greater than or equal to proposal_limit"
            )
        return self

    def resolve_candidate_intent_pool_limit(self) -> int:
        if self.candidate_intent_pool_limit is not None:
            return self.candidate_intent_pool_limit
        derived = math.ceil(self.proposal_limit * 2.5)
        return max(12, min(derived, 24))


class ApprovalConfig(ParityModel):
    label: str = "parity:approve"


class AutoRunConfig(ParityModel):
    enabled: bool = True
    fail_on: str | None = "regression_guard"
    notify: str | None = "pr_comment"


class SpendConfig(ParityModel):
    analysis_total_spend_cap_usd: float | None = None
    stage1_agent_cap_usd: float | None = None
    stage2_agent_cap_usd: float | None = None
    stage2_embedding_cap_usd: float | None = None
    stage3_agent_cap_usd: float | None = None
    budget_policy: Literal["auto", "static", "carryforward"] = "auto"

    @field_validator(
        "analysis_total_spend_cap_usd",
        "stage1_agent_cap_usd",
        "stage2_agent_cap_usd",
        "stage2_embedding_cap_usd",
        "stage3_agent_cap_usd",
    )
    @classmethod
    def validate_positive_optional_floats(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("spend caps must be positive when provided")
        return value

    @model_validator(mode="after")
    def validate_stage_override_shape(self) -> "SpendConfig":
        stage_caps = [
            self.stage1_agent_cap_usd,
            self.stage2_agent_cap_usd,
            self.stage2_embedding_cap_usd,
            self.stage3_agent_cap_usd,
        ]
        any_stage_caps = any(value is not None for value in stage_caps)
        all_stage_caps = all(value is not None for value in stage_caps)
        if any_stage_caps and not all_stage_caps:
            raise ValueError(
                "stage-specific spend overrides must all be set together: "
                "stage1_agent_cap_usd, stage2_agent_cap_usd, stage2_embedding_cap_usd, "
                "and stage3_agent_cap_usd"
            )
        if all_stage_caps:
            total = sum(value for value in stage_caps if value is not None)
            configured_total = self.analysis_total_spend_cap_usd
            if configured_total is not None and not math.isclose(configured_total, total, abs_tol=0.01):
                raise ValueError(
                    "analysis_total_spend_cap_usd must match the sum of the explicit stage-specific spend caps"
                )
        return self


@dataclass(slots=True, frozen=True)
class ResolvedSpendCaps:
    analysis_total_spend_cap_usd: float
    stage1_agent_cap_usd: float
    stage2_agent_cap_usd: float
    stage2_embedding_cap_usd: float
    stage3_agent_cap_usd: float
    source: Literal["default_total", "explicit_total", "explicit_stage_overrides"]


class ParityConfig(ParityModel):
    version: int = 2
    behavior_artifacts: ArtifactDetectionConfig = Field(default_factory=ArtifactDetectionConfig)
    guardrail_artifacts: ArtifactDetectionConfig = Field(default_factory=ArtifactDetectionConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    evals: EvalsConfig = Field(default_factory=EvalsConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    similarity: SimilarityConfig = Field(default_factory=SimilarityConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    auto_run: AutoRunConfig = Field(default_factory=AutoRunConfig)
    spend: SpendConfig = Field(default_factory=SpendConfig)

    @classmethod
    def load(cls, path: str | Path = "parity.yaml", *, allow_missing: bool = False) -> Self:
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
            raise ConfigError(f"Invalid parity configuration: {exc}") from exc

    def resolve_path(self, relative_path: str | Path, repo_root: str | Path | None = None) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            return path
        base = Path(repo_root) if repo_root is not None else Path.cwd()
        return base / path

    def find_eval_rule(self, artifact_path: str) -> EvalRuleConfig | None:
        for rule in self.evals.rules:
            if rule.matches(artifact_path):
                return rule
        return None

    def resolve_platform_discovery_order(self, preferred_platform: PlatformName | None = None) -> list[PlatformName]:
        ordered: list[PlatformName] = []
        if preferred_platform is not None:
            ordered.append(preferred_platform)
        for platform in self.evals.discovery.platform_discovery_order:
            if platform not in ordered:
                ordered.append(platform)
        for platform in ("langsmith", "braintrust", "arize_phoenix", "promptfoo"):
            if getattr(self.platforms, platform, None) is not None and platform not in ordered:
                ordered.append(platform)  # type: ignore[arg-type]
        if "promptfoo" not in ordered and self.evals.discovery.allow_repo_asset_discovery:
            ordered.append("promptfoo")
        return ordered

    def resolve_spend_caps(self) -> ResolvedSpendCaps:
        spend = self.spend
        stage_caps = [
            spend.stage1_agent_cap_usd,
            spend.stage2_agent_cap_usd,
            spend.stage2_embedding_cap_usd,
            spend.stage3_agent_cap_usd,
        ]
        if all(value is not None for value in stage_caps):
            return ResolvedSpendCaps(
                analysis_total_spend_cap_usd=sum(value for value in stage_caps if value is not None),
                stage1_agent_cap_usd=spend.stage1_agent_cap_usd or 0.0,
                stage2_agent_cap_usd=spend.stage2_agent_cap_usd or 0.0,
                stage2_embedding_cap_usd=spend.stage2_embedding_cap_usd or 0.0,
                stage3_agent_cap_usd=spend.stage3_agent_cap_usd or 0.0,
                source="explicit_stage_overrides",
            )

        total = spend.analysis_total_spend_cap_usd or DEFAULT_ANALYSIS_TOTAL_SPEND_CAP_USD
        source: Literal["default_total", "explicit_total"] = (
            "explicit_total" if spend.analysis_total_spend_cap_usd is not None else "default_total"
        )
        return ResolvedSpendCaps(
            analysis_total_spend_cap_usd=total,
            stage1_agent_cap_usd=total * DEFAULT_STAGE1_AGENT_SPEND_RATIO,
            stage2_agent_cap_usd=total * DEFAULT_STAGE2_AGENT_SPEND_RATIO,
            stage2_embedding_cap_usd=total * DEFAULT_STAGE2_EMBEDDING_SPEND_RATIO,
            stage3_agent_cap_usd=total * DEFAULT_STAGE3_AGENT_SPEND_RATIO,
            source=source,
        )


# Backward-compatible internal alias while init/workflow helpers are updated.
MappingConfig = EvalRuleConfig
