from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from probegen.models._base import ProbegenModel
from probegen.models.eval_case import ConversationMessage, is_conversation_input

ProbeType = Literal[
    "regression_guard",
    "expected_improvement",
    "boundary_probe",
    "edge_case",
    "overcorrection_probe",
    "tool_selection_probe",
    "ambiguity_probe",
    "judge_calibration_probe",
]

InputFormat = Literal["string", "dict", "conversation"]
ExpectedBehaviorType = Literal["exact_output", "contains", "not_contains", "llm_rubric", "format_check"]


class ProbeCase(ProbegenModel):
    probe_id: str
    gap_id: str
    probe_type: ProbeType
    is_conversational: bool
    input: str | dict[str, Any] | list[ConversationMessage] | list[dict[str, Any]]
    input_format: InputFormat
    expected_behavior: str
    expected_behavior_type: ExpectedBehaviorType
    rubric: str | None = None
    probe_rationale: str
    related_risk_flag: str
    nearest_existing_case_id: str | None = None
    nearest_existing_similarity: float | None = None
    specificity_confidence: float
    testability_confidence: float
    realism_confidence: float
    approved: bool = False

    @field_validator(
        "specificity_confidence",
        "testability_confidence",
        "realism_confidence",
        "nearest_existing_similarity",
    )
    @classmethod
    def validate_probability_fields(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence and similarity values must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_input_shape(self) -> "ProbeCase":
        if self.input_format == "conversation":
            if not is_conversation_input(
                [message.model_dump() if isinstance(message, ConversationMessage) else message for message in self.input]
            ):
                raise ValueError("conversation probes require a list of role/content messages")
        elif self.input_format == "string" and not isinstance(self.input, str):
            raise ValueError("string probes require string input")
        elif self.input_format == "dict" and not isinstance(self.input, dict):
            raise ValueError("dict probes require object input")
        return self


class ExportFormats(ProbegenModel):
    promptfoo: str | None = None
    deepeval: str | None = None
    raw_json: str | None = None


class ProbeProposal(ProbegenModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    stage1_run_id: str
    stage2_run_id: str
    timestamp: datetime
    pr_number: int
    commit_sha: str
    probe_count: int
    probes: list[ProbeCase] = Field(default_factory=list)
    export_formats: ExportFormats = Field(default_factory=ExportFormats)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_probe_count(self) -> "ProbeProposal":
        if self.probe_count != len(self.probes):
            self.probe_count = len(self.probes)
        return self
