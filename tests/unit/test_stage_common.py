from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from parity.errors import BudgetExceededError, StageError
from parity.stages import _common


class _OutputModel(BaseModel):
    ok: bool


class _NormalizedDateModel(BaseModel):
    last_verified_at: datetime | None = None


class _FakeContentBlock:
    def __init__(self, *, text: str | None = None, name: str | None = None) -> None:
        self.text = text
        self.name = name


class _FakeAssistantMessage:
    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        error: str | None = None,
        content: list[_FakeContentBlock] | None = None,
    ) -> None:
        self.model = model
        self.error = error
        self.content = content or []


class _FakeTaskProgressMessage:
    def __init__(self, *, usage: dict | None = None, last_tool_name: str | None = None) -> None:
        self.usage = usage or {}
        self.last_tool_name = last_tool_name


class _FakeResultMessage:
    def __init__(
        self,
        *,
        subtype: str = "success",
        structured_output=None,
        result=None,
        total_cost_usd: float = 0.12,
        duration_ms: int = 321,
        num_turns: int = 2,
        is_error: bool = False,
    ) -> None:
        self.subtype = subtype
        self.structured_output = structured_output
        self.result = result
        self.total_cost_usd = total_cost_usd
        self.duration_ms = duration_ms
        self.num_turns = num_turns
        self.is_error = is_error


def test_run_query_counts_assistant_tool_calls_when_progress_underreports(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(*, prompt: str, options) -> object:
        yield _FakeAssistantMessage(
            content=[_FakeContentBlock(name="mcp__parity_stage2__fetch_eval_target_snapshot")]
        )
        yield _FakeResultMessage(
            structured_output={"ok": True},
            result='{"ok": true}',
        )

    monkeypatch.setattr(_common, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(_common, "TaskProgressMessage", _FakeTaskProgressMessage)
    monkeypatch.setattr(_common, "ResultMessage", _FakeResultMessage)
    monkeypatch.setattr(_common, "query", fake_query)

    result = asyncio.run(
        _common._run_query(
            stage_num=2,
            prompt="test",
            options=SimpleNamespace(max_turns=10, max_budget_usd=1.0),
            output_model=_OutputModel,
        )
    )

    assert result.extras is not None
    assert result.extras["observed_tool_uses"] == 1
    assert result.extras["tools_observed"] == [
        {
            "name": "mcp__parity_stage2__fetch_eval_target_snapshot",
            "count": 1,
            "approx_duration_ms": 0,
        }
    ]


def test_run_query_preserves_failure_metadata_on_turn_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(*, prompt: str, options) -> object:
        yield _FakeAssistantMessage(
            content=[_FakeContentBlock(name="mcp__parity_stage2__fetch_eval_target_snapshot")]
        )
        yield _FakeResultMessage(
            subtype="error_max_turns",
            structured_output=None,
            result="{}",
            total_cost_usd=0.45,
            duration_ms=987,
            num_turns=40,
        )

    monkeypatch.setattr(_common, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(_common, "TaskProgressMessage", _FakeTaskProgressMessage)
    monkeypatch.setattr(_common, "ResultMessage", _FakeResultMessage)
    monkeypatch.setattr(_common, "query", fake_query)

    with pytest.raises(BudgetExceededError) as exc_info:
        asyncio.run(
            _common._run_query(
                stage_num=2,
                prompt="test",
                options=SimpleNamespace(max_turns=40, max_budget_usd=0.45),
                output_model=_OutputModel,
            )
        )

    assert exc_info.value.details["subtype"] == "error_max_turns"
    assert exc_info.value.details["model"] == "claude-sonnet-4-6"
    assert exc_info.value.details["duration_ms"] == 987
    assert exc_info.value.details["num_turns"] == 40
    assert exc_info.value.details["observed_tool_uses"] == 1


def test_run_query_applies_normalize_payload_before_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(*, prompt: str, options) -> object:
        yield _FakeResultMessage(
            structured_output={"last_verified_at": ""},
            result='{"last_verified_at": ""}',
        )

    monkeypatch.setattr(_common, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(_common, "TaskProgressMessage", _FakeTaskProgressMessage)
    monkeypatch.setattr(_common, "ResultMessage", _FakeResultMessage)
    monkeypatch.setattr(_common, "query", fake_query)

    result = asyncio.run(
        _common._run_query(
            stage_num=2,
            prompt="test",
            options=SimpleNamespace(max_turns=10, max_budget_usd=1.0),
            output_model=_NormalizedDateModel,
            normalize_payload=lambda payload: {
                **payload,
                "last_verified_at": None if payload.get("last_verified_at") == "" else payload.get("last_verified_at"),
            },
        )
    )

    assert result.data.last_verified_at is None


def test_classify_stage_failure_parses_anthropic_billing_error_payload() -> None:
    failure = _common.classify_stage_failure(
        raw_result='{"type":"error","error":{"type":"billing_error","message":"Your organization is out of credits."},"request_id":"req_abc123"}'
    )

    assert failure["category"] == "billing"
    assert failure["provider"] == "anthropic"
    assert failure["http_status"] == 402
    assert failure["provider_error_type"] == "billing_error"
    assert failure["request_id"] == "req_abc123"


def test_classify_stage_failure_parses_anthropic_invalid_request_payload() -> None:
    failure = _common.classify_stage_failure(
        raw_result='{"type":"error","error":{"type":"invalid_request_error","message":"Prompt is too large."},"request_id":"req_invalid400"}'
    )

    assert failure["category"] == "provider_invalid_request"
    assert failure["provider"] == "anthropic"
    assert failure["http_status"] == 400
    assert failure["provider_error_type"] == "invalid_request_error"
    assert failure["request_id"] == "req_invalid400"


def test_run_query_classifies_provider_errors_and_captures_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_query(*, prompt: str, options) -> object:
        yield _FakeResultMessage(
            subtype="error_during_execution",
            is_error=True,
            result='{"type":"error","error":{"type":"billing_error","message":"Your organization is out of credits."},"request_id":"req_billing001"}',
        )

    monkeypatch.setattr(_common, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(_common, "TaskProgressMessage", _FakeTaskProgressMessage)
    monkeypatch.setattr(_common, "ResultMessage", _FakeResultMessage)
    monkeypatch.setattr(_common, "query", fake_query)

    with pytest.raises(StageError) as exc_info:
        asyncio.run(
            _common._run_query(
                stage_num=2,
                prompt="test",
                options=SimpleNamespace(
                    max_turns=10,
                    max_budget_usd=1.0,
                    output_format={"type": "json_schema", "schema": {"type": "object", "properties": {}}},
                    extra_args={},
                ),
                output_model=_OutputModel,
            )
        )

    failure = exc_info.value.details["failure"]
    diagnostics = exc_info.value.details["diagnostics"]
    assert failure["category"] == "billing"
    assert failure["request_id"] == "req_billing001"
    assert diagnostics["failure"]["category"] == "billing"
    assert diagnostics["result_subtype"] == "error_during_execution"


def test_build_metadata_excludes_diagnostics_and_debug_log_lines() -> None:
    metadata = _common.build_metadata(
        2,
        _common.StageRunResult(
            data={"ok": True},
            model="claude-sonnet-4-6",
            cost_usd=0.1,
            duration_ms=123,
            num_turns=2,
            timestamp="2026-04-01T00:00:00Z",
            extras={
                "assistant_messages": 1,
                "diagnostics": {"completed": True},
                "debug_log_lines": ["debug"],
            },
        ),
    )

    assert metadata["assistant_messages"] == 1
    assert "diagnostics" not in metadata
    assert "debug_log_lines" not in metadata
