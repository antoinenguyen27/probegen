from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from probegen.models._base import ProbegenModel

RiskLevel = Literal["low", "medium", "high"]
Alignment = Literal["confirmed", "contradicted", "unknown"]
SimilarityClassification = Literal["duplicate", "boundary", "related", "novel"]
GapType = Literal["covered", "boundary_shift", "uncovered"]
GuardrailDirection = Literal["should_catch", "should_pass"]


class BehaviorChange(ProbegenModel):
    artifact_path: str
    artifact_type: str
    artifact_class: str
    change_type: str
    inferred_intent: str
    pr_description_alignment: Alignment
    unintended_risk_flags: list[str] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    false_negative_risks: list[str] = Field(default_factory=list)
    false_positive_risks: list[str] = Field(default_factory=list)
    change_summary: str
    before_hash: str | None = None
    after_hash: str | None = None


class CompoundChange(ProbegenModel):
    artifact_paths: list[str] = Field(default_factory=list)
    summary: str


class BehaviorChangeManifest(ProbegenModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    pr_number: int
    commit_sha: str
    timestamp: datetime
    has_changes: bool
    overall_risk: RiskLevel
    pr_intent_summary: str
    pr_description_alignment: Alignment
    compound_change_detected: bool
    changes: list[BehaviorChange] = Field(default_factory=list)
    compound_changes: list[CompoundChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_change_gate_consistency(self) -> "BehaviorChangeManifest":
        if self.has_changes and not self.changes:
            raise ValueError("has_changes cannot be true when changes is empty")
        if not self.has_changes:
            self.changes = []
        return self


class CoverageSummary(ProbegenModel):
    total_relevant_cases: int = 0
    cases_covering_changed_behavior: int = 0
    coverage_ratio: float = 0.0
    platform: str | None = None
    dataset: str | None = None

    @field_validator("coverage_ratio")
    @classmethod
    def validate_ratio(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("coverage_ratio must be between 0 and 1")
        return value


class NearestExistingCase(ProbegenModel):
    case_id: str
    input_normalized: str
    similarity: float
    classification: SimilarityClassification

    @field_validator("similarity")
    @classmethod
    def validate_similarity(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("similarity must be between 0 and 1")
        return value


class CoverageGap(ProbegenModel):
    gap_id: str
    artifact_path: str
    gap_type: GapType
    related_risk_flag: str
    description: str
    nearest_existing_cases: list[NearestExistingCase] = Field(default_factory=list)
    priority: RiskLevel
    guardrail_direction: GuardrailDirection | None = None
    is_conversational: bool = False


class CoverageGapManifest(ProbegenModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    stage1_run_id: str
    timestamp: datetime
    unmapped_artifacts: list[str] = Field(default_factory=list)
    coverage_summary: CoverageSummary | None = None
    gaps: list[CoverageGap] = Field(default_factory=list)
