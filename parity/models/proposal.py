from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from parity.models._base import ParityModel
from parity.models.eval_case import ConversationMessage, MethodKind, is_conversation_input
from parity.models.topology import EvalTargetProfile, EvaluatorScope, ExecutionSurface

IntentType = Literal[
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
WriteStatus = Literal["native_ready", "review_only", "unsupported"]
EvaluatorAction = Literal["none", "row_local", "reuse_existing", "manual"]
RenderingKind = Literal[
    "langsmith_example",
    "braintrust_record",
    "phoenix_example",
    "promptfoo_test",
    "file_patch",
    "review_note",
]


def _validate_probability_field(value: float | None) -> float | None:
    if value is None:
        return value
    if not 0.0 <= value <= 1.0:
        raise ValueError("confidence and similarity values must be between 0 and 1")
    return value


class ConversationMessageDraft(ParityModel):
    role: str | None = None
    content: str | None = None


class ProbeIntentDraft(ParityModel):
    intent_id: str
    gap_id: str
    intent_type: IntentType
    title: str
    input_format: InputFormat
    string_input: str | None = None
    dict_input: dict[str, Any] | None = None
    conversation_input: list[ConversationMessageDraft] = Field(default_factory=list)
    behavior_under_test: str
    pass_criteria: str
    failure_mode: str
    probe_rationale: str
    nearest_existing_case_id: str | None = None
    nearest_existing_similarity: float | None = None
    specificity_confidence: float
    testability_confidence: float
    novelty_confidence: float
    realism_confidence: float
    target_fit_confidence: float

    @field_validator(
        "specificity_confidence",
        "testability_confidence",
        "novelty_confidence",
        "realism_confidence",
        "target_fit_confidence",
        "nearest_existing_similarity",
    )
    @classmethod
    def validate_probability_fields(cls, value: float | None) -> float | None:
        return _validate_probability_field(value)


class ProbeIntent(ParityModel):
    intent_id: str
    gap_id: str
    target_id: str
    method_kind: MethodKind
    intent_type: IntentType
    title: str
    is_conversational: bool
    input: str | dict[str, Any] | list[ConversationMessage] | list[dict[str, Any]]
    input_format: InputFormat
    behavior_under_test: str
    pass_criteria: str
    failure_mode: str
    probe_rationale: str
    related_risk_flag: str
    native_input_binding: str | None = None
    native_output_binding: str | None = None
    native_reference_output: Any | None = None
    evaluator_dossier_id: str | None = None
    preferred_evaluator_binding: str | None = None
    native_metadata_hints: dict[str, Any] = Field(default_factory=dict)
    native_tag_hints: list[str] = Field(default_factory=list)
    native_assertion_hints: list[str] = Field(default_factory=list)
    native_shape_notes: list[str] = Field(default_factory=list)
    nearest_existing_case_id: str | None = None
    nearest_existing_similarity: float | None = None
    specificity_confidence: float
    testability_confidence: float
    novelty_confidence: float
    realism_confidence: float
    target_fit_confidence: float

    @field_validator(
        "specificity_confidence",
        "testability_confidence",
        "novelty_confidence",
        "realism_confidence",
        "target_fit_confidence",
        "nearest_existing_similarity",
    )
    @classmethod
    def validate_probability_fields(cls, value: float | None) -> float | None:
        return _validate_probability_field(value)

    @model_validator(mode="after")
    def validate_input_shape(self) -> "ProbeIntent":
        if self.input_format == "conversation":
            serialized = [
                message.model_dump() if isinstance(message, ConversationMessage) else message for message in self.input
            ]
            if not is_conversation_input(serialized):
                raise ValueError("conversation intents require role/content messages")
        elif self.input_format == "string" and not isinstance(self.input, str):
            raise ValueError("string intents require string input")
        elif self.input_format == "dict" and not isinstance(self.input, dict):
            raise ValueError("dict intents require object input")
        return self


class NativeEvalRendering(ParityModel):
    rendering_id: str
    intent_id: str
    target_id: str
    method_kind: MethodKind
    rendering_kind: RenderingKind
    renderer_id: str
    write_status: WriteStatus
    render_confidence: float
    target_locator: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    file_path: str | None = None
    patch: str | None = None
    summary: str | None = None
    abstention_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("render_confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("render_confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_rendering_shape(self) -> "NativeEvalRendering":
        if self.rendering_kind == "file_patch" and not self.patch:
            raise ValueError("file_patch renderings require patch")
        if self.rendering_kind not in {"file_patch", "review_note"} and not self.payload:
            raise ValueError(f"{self.rendering_kind} renderings require payload")
        if self.write_status == "unsupported" and not self.abstention_reason:
            raise ValueError("unsupported renderings require abstention_reason")
        return self


class EvaluatorPlan(ParityModel):
    plan_id: str
    intent_id: str
    target_id: str
    action: EvaluatorAction
    scope: EvaluatorScope = "unknown"
    execution_surface: ExecutionSurface = "unknown"
    evaluator_dossier_id: str | None = None
    binding_ref: str | None = None
    binding_label: str | None = None
    confidence: float = 0.0
    requires_opt_in: bool = False
    rationale: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return value


class RenderArtifact(ParityModel):
    target_id: str
    artifact_kind: str
    path: str
    write_status: WriteStatus


class EvalIntentCandidateBundle(ParityModel):
    intents: list[ProbeIntentDraft] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvalProposalManifest(ParityModel):
    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    stage1_run_id: str
    stage2_run_id: str
    stage3_run_id: str
    timestamp: datetime
    pr_number: int
    commit_sha: str
    intent_count: int
    targets: list[EvalTargetProfile] = Field(default_factory=list)
    intents: list[ProbeIntent] = Field(default_factory=list)
    evaluator_plans: list[EvaluatorPlan] = Field(default_factory=list)
    renderings: list[NativeEvalRendering] = Field(default_factory=list)
    render_artifacts: list[RenderArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> "EvalProposalManifest":
        target_ids = {target.target_id for target in self.targets}
        for intent in self.intents:
            if intent.target_id not in target_ids:
                raise ValueError(f"intent target_id `{intent.target_id}` does not exist in targets")
        for rendering in self.renderings:
            if rendering.target_id not in target_ids:
                raise ValueError(f"rendering target_id `{rendering.target_id}` does not exist in targets")
            if not any(intent.intent_id == rendering.intent_id for intent in self.intents):
                raise ValueError(f"rendering intent_id `{rendering.intent_id}` does not exist in intents")
        for plan in self.evaluator_plans:
            if plan.target_id not in target_ids:
                raise ValueError(f"evaluator plan target_id `{plan.target_id}` does not exist in targets")
            if not any(intent.intent_id == plan.intent_id for intent in self.intents):
                raise ValueError(f"evaluator plan intent_id `{plan.intent_id}` does not exist in intents")
        if self.intent_count != len(self.intents):
            self.intent_count = len(self.intents)
        return self
