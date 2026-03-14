from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query

from probegen.errors import BudgetExceededError, RateLimitStageError, SchemaValidationError, StageError

ModelT = TypeVar("ModelT")


@dataclass(slots=True)
class StageRunResult:
    data: Any
    model: str | None
    cost_usd: float | None
    duration_ms: int
    num_turns: int
    timestamp: str
    raw_result: str | None = None
    extras: dict[str, Any] | None = None


def message_text(message: AssistantMessage) -> str:
    chunks: list[str] = []
    for block in message.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def attempt_partial_extraction(raw_result: str | None) -> Any | None:
    if not raw_result:
        return None
    try:
        return json.loads(raw_result)
    except json.JSONDecodeError:
        start = raw_result.find("{")
        end = raw_result.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw_result[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


async def _run_query(
    *,
    stage_num: int,
    prompt: str,
    options: ClaudeAgentOptions,
    output_model: Any,
) -> StageRunResult:
    last_model: str | None = None
    last_assistant_error: str | None = None
    result_message: ResultMessage | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            last_model = message.model
            if message.error:
                last_assistant_error = message.error
        elif isinstance(message, ResultMessage):
            result_message = message

    if last_assistant_error == "rate_limit":
        raise RateLimitStageError(
            "rate_limit",
            stage=stage_num,
            retry_count=1,
        )

    if result_message is None:
        raise StageError("No ResultMessage received from Agent SDK", stage=stage_num)

    raw_result: Any = (
        result_message.structured_output
        if result_message.structured_output is not None
        else result_message.result
    )
    if result_message.subtype in {"error_max_budget_usd", "error_max_turns"}:
        partial = attempt_partial_extraction(result_message.result)
        raise BudgetExceededError(
            "Agent SDK limit reached",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            partial_result=partial,
        )
    if result_message.is_error:
        raise StageError(
            result_message.result or "Agent SDK error",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
        )

    try:
        parsed_payload = raw_result if isinstance(raw_result, dict) else json.loads(raw_result or "{}")
        parsed = output_model.model_validate(parsed_payload)
    except Exception as exc:
        raise SchemaValidationError(f"Stage {stage_num} output failed validation: {exc}") from exc

    return StageRunResult(
        data=parsed,
        model=last_model,
        cost_usd=result_message.total_cost_usd,
        duration_ms=result_message.duration_ms,
        num_turns=result_message.num_turns,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        raw_result=result_message.result,
    )


async def run_stage_with_retry(
    *,
    stage_num: int,
    prompt: str,
    options: ClaudeAgentOptions,
    output_model: Any,
    max_retries: int = 3,
) -> StageRunResult:
    waits = [30, 60, 120]
    for attempt in range(max_retries):
        try:
            return await _run_query(
                stage_num=stage_num,
                prompt=prompt,
                options=options,
                output_model=output_model,
            )
        except RateLimitStageError:
            if attempt >= max_retries - 1:
                raise BudgetExceededError(
                    "Rate limit persisted after retries",
                    stage=stage_num,
                ) from None
            await asyncio.sleep(waits[attempt])
    raise StageError(f"Stage {stage_num} failed after retries", stage=stage_num)


def build_metadata(stage: int, result: StageRunResult, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "stage": stage,
        "model": result.model,
        "cost_usd": result.cost_usd,
        "duration_ms": result.duration_ms,
        "num_turns": result.num_turns,
        "timestamp": result.timestamp,
    }
    if result.extras:
        metadata.update(result.extras)
    if extra:
        metadata.update(extra)
    return metadata
