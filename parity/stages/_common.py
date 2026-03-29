from __future__ import annotations

import asyncio
import copy
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TaskProgressMessage,
    query,
)

from parity.errors import BudgetExceededError, RateLimitStageError, SchemaValidationError, StageError

ModelT = TypeVar("ModelT")

_SUPPORTED_KEYS = {"type", "properties", "required", "items", "enum", "const", "$ref", "$defs"}


def simplify_schema(schema: dict, *, remove_keys: set[str] | None = None) -> dict:
    """Return a schema containing only Agent SDK CLI-supported keywords.

    Strips unsupported keywords (additionalProperties, title, default, format, anyOf),
    dereferences $defs/$ref, and removes inject_fields keys from properties/required
    so the CLI does not expect the agent to produce orchestrator-owned values.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(obj: Any) -> Any:
        if isinstance(obj, list):
            return [resolve(item) for item in obj]
        if not isinstance(obj, dict):
            return obj

        # Inline $ref
        if "$ref" in obj and len(obj) == 1:
            ref_name = obj["$ref"].split("/")[-1]
            return resolve(copy.deepcopy(defs.get(ref_name, {})))

        # Resolve anyOf: [{X}, {type: null}] → X; complex → unconstrained {}
        if "anyOf" in obj:
            non_null = [v for v in obj["anyOf"] if v.get("type") != "null"]
            if len(non_null) == 1:
                return resolve(non_null[0])
            return {}

        result = {k: v for k, v in obj.items() if k in _SUPPORTED_KEYS}
        if "properties" in result:
            result["properties"] = {k: resolve(v) for k, v in result["properties"].items()}
        if "items" in result:
            result["items"] = resolve(result["items"])
        if "$defs" in result:
            result["$defs"] = {k: resolve(v) for k, v in result["$defs"].items()}
        return result

    simplified = resolve(schema)

    if remove_keys and isinstance(simplified.get("properties"), dict):
        for key in remove_keys:
            simplified["properties"].pop(key, None)
        if "required" in simplified:
            simplified["required"] = [k for k in simplified["required"] if k not in remove_keys]

    return simplified


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


def message_tool_names(message: AssistantMessage) -> list[str]:
    names: list[str] = []
    for block in message.content:
        name = getattr(block, "name", None)
        if isinstance(name, str):
            names.append(name)
    return names


def format_tool_summary(
    tool_counts: dict[str, int],
    tool_durations_ms: dict[str, int],
) -> str:
    if not tool_counts:
        return "none"

    parts: list[str] = []
    for tool_name in sorted(tool_counts):
        count = tool_counts[tool_name]
        duration_ms = tool_durations_ms.get(tool_name, 0)
        if duration_ms > 0:
            parts.append(f"{tool_name} x{count} (~{duration_ms}ms)")
        else:
            parts.append(f"{tool_name} x{count}")
    return ", ".join(parts)


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
    inject_fields: dict[str, Any] | None = None,
    normalize_payload: Any | None = None,
) -> StageRunResult:
    last_model: str | None = None
    last_assistant_error: str | None = None
    result_message: ResultMessage | None = None
    assistant_message_count = 0
    observed_tool_counts: dict[str, int] = defaultdict(int)
    observed_tool_durations_ms: dict[str, int] = defaultdict(int)
    previous_tool_uses = 0
    previous_progress_duration_ms = 0
    previous_progress_tool_name: str | None = None

    print(
        f"[stage-{stage_num}] Agent starting — max_turns={options.max_turns} budget=${options.max_budget_usd:.2f}",
        file=sys.stderr,
        flush=True,
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            assistant_message_count += 1
            last_model = message.model
            if message.error:
                last_assistant_error = message.error
            else:
                preview = message_text(message)[:120].replace("\n", " ").strip()
                if preview:
                    print(
                        f"[stage-{stage_num}] assistant_message {assistant_message_count}: {preview}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    tool_names = message_tool_names(message)
                    if tool_names:
                        preview_names = ", ".join(tool_names[:3])
                        suffix = "" if len(tool_names) <= 3 else ", ..."
                        detail = f"tool_calls={preview_names}{suffix}"
                    else:
                        detail = "no_text"
                    print(
                        f"[stage-{stage_num}] assistant_message {assistant_message_count}: ({detail})",
                        file=sys.stderr,
                        flush=True,
                    )
        elif isinstance(message, TaskProgressMessage):
            total_tool_uses = max(message.usage.get("tool_uses", 0), 0)
            total_duration_ms = max(message.usage.get("duration_ms", 0), 0)
            last_tool_name = message.last_tool_name or "unknown"

            duration_delta_ms = max(total_duration_ms - previous_progress_duration_ms, 0)
            if duration_delta_ms:
                observed_tool_durations_ms[last_tool_name] += duration_delta_ms

            tool_use_delta = max(total_tool_uses - previous_tool_uses, 0)
            if tool_use_delta:
                observed_tool_counts[last_tool_name] += tool_use_delta

            if tool_use_delta or last_tool_name != previous_progress_tool_name:
                print(
                    f"[stage-{stage_num}] progress: last_tool={last_tool_name} "
                    f"cumulative_tool_uses={total_tool_uses} total_tokens={message.usage.get('total_tokens', 'n/a')} "
                    f"duration={total_duration_ms}ms",
                    file=sys.stderr,
                    flush=True,
                )

            previous_tool_uses = total_tool_uses
            previous_progress_duration_ms = total_duration_ms
            previous_progress_tool_name = last_tool_name
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
    if result_message.subtype == "error_max_budget_usd":
        partial = attempt_partial_extraction(result_message.result)
        raise BudgetExceededError(
            "Cost budget exceeded",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            partial_result=partial,
        )
    if result_message.subtype == "error_max_turns":
        partial = attempt_partial_extraction(result_message.result)
        raise BudgetExceededError(
            "Max turns limit reached — increase max_turns or simplify the stage prompt",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            partial_result=partial,
        )
    if result_message.subtype == "error_max_structured_output_retries":
        truncated = (
            str(result_message.result or "")[:300]
        ).replace("\n", "\\n")
        raise SchemaValidationError(
            f"Stage {stage_num} structured output failed after all retries — "
            f"the agent could not produce JSON matching the required schema. "
            f"Check that the prompt clearly describes all required fields.\n"
            f"Raw response (first 300 chars): {truncated}"
        )
    if result_message.is_error:
        raise StageError(
            result_message.result or "Agent SDK error",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
        )

    if result_message.structured_output is None:
        truncated = str(result_message.result or "")[:300].replace("\n", "\\n")
        raise SchemaValidationError(
            f"Stage {stage_num} structured output was not populated "
            f"(structured_output=None, subtype={result_message.subtype!r}). "
            f"The CLI did not enforce the JSON schema — check schema compatibility with the Agent SDK.\n"
            f"Raw response (first 300 chars): {truncated}"
        )

    try:
        # structured_output is a validated dict from the SDK
        if isinstance(raw_result, dict):
            parsed_payload = raw_result
        else:
            parsed_payload = json.loads(raw_result or "{}")

        if inject_fields:
            parsed_payload.update(inject_fields)
        if normalize_payload is not None:
            parsed_payload = normalize_payload(parsed_payload)
        parsed = output_model.model_validate(parsed_payload)
    except Exception as exc:
        # Capture raw result for debugging: show first 300 chars with escaped newlines
        truncated_result = (
            str(raw_result)[:300] if raw_result else "(empty/None)"
        ).replace("\n", "\\n")
        raise SchemaValidationError(
            f"Stage {stage_num} output failed validation: {exc}\n"
            f"Raw response (first 300 chars): {truncated_result}"
        ) from exc

    print(
        f"[stage-{stage_num}] completion: sdk_turns={result_message.num_turns} "
        f"assistant_messages={assistant_message_count} observed_tool_uses={sum(observed_tool_counts.values())}",
        file=sys.stderr,
        flush=True,
    )
    if observed_tool_counts:
        print(
            f"[stage-{stage_num}] tool_summary: {format_tool_summary(observed_tool_counts, observed_tool_durations_ms)}",
            file=sys.stderr,
            flush=True,
        )

    return StageRunResult(
        data=parsed,
        model=last_model,
        cost_usd=result_message.total_cost_usd,
        duration_ms=result_message.duration_ms,
        num_turns=result_message.num_turns,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        raw_result=result_message.result,
        extras={
            "assistant_messages": assistant_message_count,
            "observed_tool_uses": sum(observed_tool_counts.values()),
            "tools_observed": [
                {
                    "name": tool_name,
                    "count": observed_tool_counts[tool_name],
                    "approx_duration_ms": observed_tool_durations_ms.get(tool_name, 0),
                }
                for tool_name in sorted(observed_tool_counts)
            ],
        },
    )


async def run_stage_with_retry(
    *,
    stage_num: int,
    prompt: str,
    options: ClaudeAgentOptions,
    output_model: Any,
    inject_fields: dict[str, Any] | None = None,
    normalize_payload: Any | None = None,
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
                inject_fields=inject_fields,
                normalize_payload=normalize_payload,
            )
        except RateLimitStageError:
            if attempt >= max_retries - 1:
                raise BudgetExceededError(
                    "Rate limit persisted after retries",
                    stage=stage_num,
                ) from None
            wait = waits[attempt]
            print(
                f"[stage-{stage_num}] Rate limited on attempt {attempt + 1}/{max_retries}. "
                f"Waiting {wait}s before retry...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(wait)
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
