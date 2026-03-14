from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from probegen.models._base import ProbegenModel

ConversationRole = Literal["system", "user", "assistant", "tool"]
InputLike = str | dict[str, Any] | list[dict[str, Any]]
SourcePlatform = Literal["langsmith", "braintrust", "phoenix", "promptfoo"]

PRIORITY_INPUT_KEYS = (
    "query",
    "input",
    "question",
    "message",
    "user_message",
    "prompt",
)


class ConversationMessage(ProbegenModel):
    role: ConversationRole
    content: str


def normalize_conversational(messages: list[dict[str, Any]] | list[ConversationMessage]) -> str:
    normalized_messages = []
    for message in messages:
        role = message.role if isinstance(message, ConversationMessage) else message.get("role")
        content = (
            message.content if isinstance(message, ConversationMessage) else message.get("content")
        )
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("Conversation messages must include string role and content fields")
        normalized_messages.append(f"{role.upper()}: {content}")
    return "\n".join(normalized_messages)


def is_conversation_input(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict) and {"role", "content"} <= set(item) for item in value
    )


def normalize_input(value: Any) -> str:
    if isinstance(value, str):
        return value

    if is_conversation_input(value):
        return normalize_conversational(value)

    if isinstance(value, dict):
        for key in PRIORITY_INPUT_KEYS:
            if key in value:
                return normalize_input(value[key])
        return json.dumps(value, sort_keys=True, ensure_ascii=True)

    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def flatten_expected_output(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("expected_behavior", "answer", "output", "expected", "response"):
            selected = value.get(key)
            if isinstance(selected, str):
                return selected
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


class EvalCase(ProbegenModel):
    id: str
    source_platform: SourcePlatform
    source_dataset_id: str
    source_dataset_name: str
    input_raw: InputLike
    input_normalized: str = ""
    is_conversational: bool = False
    expected_output: str | None = None
    rubric: str | None = None
    assertion_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    embedding_model: str | None = None

    @model_validator(mode="before")
    @classmethod
    def populate_normalized_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        raw = value.get("input_raw")
        if raw is not None:
            value.setdefault("is_conversational", is_conversation_input(raw))
            value.setdefault("input_normalized", normalize_input(raw))
        if "expected_output" in value:
            value["expected_output"] = flatten_expected_output(value["expected_output"])
        return value

    @field_validator("embedding")
    @classmethod
    def ensure_embedding_values(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("Embedding vectors must not be empty")
        return value
