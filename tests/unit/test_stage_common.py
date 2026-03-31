from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from parity.errors import BudgetExceededError
from parity.stages import _common


class _OutputModel(BaseModel):
    ok: bool


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
