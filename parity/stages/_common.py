from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, is_dataclass, replace
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
_REQUEST_ID_PATTERN = re.compile(r"\breq_[A-Za-z0-9]+\b")
_ANTHROPIC_ERROR_TYPES: dict[str, tuple[int, str, bool, bool, str]] = {
    "invalid_request_error": (
        400,
        "provider_invalid_request",
        False,
        True,
        "Inspect the Anthropic error message and adjust the request payload or model configuration before rerunning the stage.",
    ),
    "authentication_error": (
        401,
        "authentication",
        False,
        True,
        "Verify the Anthropic API key used by this run and rerun the stage.",
    ),
    "billing_error": (
        402,
        "billing",
        False,
        True,
        "Add Anthropic credits or enable auto-reload for the organization, then rerun the stage.",
    ),
    "permission_error": (
        403,
        "permission",
        False,
        True,
        "Check that the Anthropic key and organization have access to the requested model or resource, then rerun the stage.",
    ),
    "not_found_error": (
        404,
        "provider_not_found",
        False,
        True,
        "Check the requested Anthropic model or resource identifier, then rerun the stage.",
    ),
    "request_too_large": (
        413,
        "provider_request_too_large",
        False,
        True,
        "Reduce the request payload size before rerunning the stage.",
    ),
    "rate_limit_error": (
        429,
        "rate_limit",
        True,
        False,
        "Retry later. Anthropic rate limiting is temporary and Parity will also retry automatically when possible.",
    ),
    "api_error": (
        500,
        "provider_api_error",
        True,
        False,
        "Retry the stage. If the issue persists, check Anthropic status and support with the request ID.",
    ),
    "overloaded_error": (
        529,
        "provider_overloaded",
        True,
        False,
        "Retry the stage later. Anthropic reports this when the service is temporarily overloaded.",
    ),
}


def _safe_preview(value: Any, *, limit: int = 300) -> str:
    preview = str(value or "")[:limit]
    return preview.replace("\n", "\\n")


def summarize_json_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None

    summary = {
        "schema_bytes": len(json.dumps(schema, sort_keys=True)),
        "object_nodes": 0,
        "array_nodes": 0,
        "required_properties": 0,
        "optional_properties": 0,
    }

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type == "object":
                summary["object_nodes"] += 1
                properties = node.get("properties", {})
                required = set(node.get("required", [])) if isinstance(node.get("required"), list) else set()
                if isinstance(properties, dict):
                    for key, value in properties.items():
                        if key in required:
                            summary["required_properties"] += 1
                        else:
                            summary["optional_properties"] += 1
                        walk(value)
                return
            if node_type == "array":
                summary["array_nodes"] += 1
                walk(node.get("items"))
                return
            for value in node.values():
                walk(value)
            return
        if isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return summary


def _drop_schema_property_path(schema: dict[str, Any], path: tuple[str, ...]) -> None:
    if not path:
        return

    head, *tail = path
    if head == "*":
        items = schema.get("items")
        if isinstance(items, dict):
            _drop_schema_property_path(items, tuple(tail))
        return

    properties = schema.get("properties")
    if not isinstance(properties, dict) or head not in properties:
        return

    if not tail:
        properties.pop(head, None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name != head]
        return

    child = properties.get(head)
    if isinstance(child, dict):
        _drop_schema_property_path(child, tuple(tail))


def simplify_schema(
    schema: dict,
    *,
    remove_keys: set[str] | None = None,
    drop_property_paths: tuple[tuple[str, ...], ...] | None = None,
) -> dict:
    """Return a schema containing only Agent SDK CLI-supported keywords.

    Strips unsupported keywords (additionalProperties, title, default, format, anyOf),
    dereferences $defs/$ref, and removes inject_fields keys from properties/required
    so the CLI does not expect the agent to produce orchestrator-owned values. Nested
    property paths can also be dropped when the original field constraints cannot be
    represented safely in the simplified schema.
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

    if drop_property_paths:
        for path in drop_property_paths:
            _drop_schema_property_path(simplified, path)

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


def merge_tool_counts(
    progress_counts: dict[str, int],
    assistant_counts: dict[str, int],
) -> dict[str, int]:
    merged: dict[str, int] = {}
    for tool_name in set(progress_counts) | set(assistant_counts):
        merged[tool_name] = max(progress_counts.get(tool_name, 0), assistant_counts.get(tool_name, 0))
    return merged


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


def _extract_anthropic_error_payload(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    payload = attempt_partial_extraction(text)
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    error_type = error.get("type")
    message = error.get("message")
    if not isinstance(error_type, str) or not isinstance(message, str):
        return None
    request_id = payload.get("request_id")
    return {
        "type": error_type,
        "message": message,
        "request_id": request_id if isinstance(request_id, str) else None,
    }


def _extract_request_id(text: str | None) -> str | None:
    if not text:
        return None
    match = _REQUEST_ID_PATTERN.search(text)
    return match.group(0) if match else None


def classify_stage_failure(
    *,
    subtype: str | None = None,
    raw_result: str | None = None,
    stderr_lines: list[str] | None = None,
    last_assistant_error: str | None = None,
    stall_reason: str | None = None,
) -> dict[str, Any]:
    stderr_lines = stderr_lines or []
    if subtype == "error_max_budget_usd":
        return {
            "category": "budget_exceeded",
            "provider": None,
            "http_status": None,
            "provider_error_type": None,
            "request_id": None,
            "retryable": False,
            "user_actionable": True,
            "summary": "Stage spend cap was exceeded.",
            "next_action": "Increase the stage budget or reduce the stage workload before retrying.",
        }
    if subtype == "error_max_turns":
        return {
            "category": "max_turns",
            "provider": None,
            "http_status": None,
            "provider_error_type": None,
            "request_id": None,
            "retryable": False,
            "user_actionable": True,
            "summary": "Stage hit the configured max-turn limit.",
            "next_action": "Increase max_turns or reduce the stage workload before retrying.",
        }
    if subtype == "error_max_structured_output_retries":
        return {
            "category": "structured_output_validation",
            "provider": None,
            "http_status": None,
            "provider_error_type": None,
            "request_id": None,
            "retryable": False,
            "user_actionable": True,
            "summary": "Claude could not satisfy the required structured output schema.",
            "next_action": "Inspect the stage diagnostics artifact and simplify the stage schema or split the output into smaller host-assembled sections.",
        }
    if stall_reason is not None:
        category = "structured_output_stall" if stall_reason == "StructuredOutput" else "transport_stall"
        summary = (
            "Claude stopped producing events while final structured output was pending."
            if stall_reason == "StructuredOutput"
            else "Claude stopped producing events before the stage completed."
        )
        next_action = (
            "Inspect the stage diagnostics artifact and Claude CLI debug log to determine whether final structured output stalled or the transport stopped responding."
        )
        return {
            "category": category,
            "provider": None,
            "http_status": None,
            "provider_error_type": None,
            "request_id": None,
            "retryable": True,
            "user_actionable": True,
            "summary": summary,
            "next_action": next_action,
        }
    if last_assistant_error == "rate_limit":
        return {
            "category": "rate_limit",
            "provider": "anthropic",
            "http_status": 429,
            "provider_error_type": "rate_limit_error",
            "request_id": _extract_request_id(raw_result) or _extract_request_id("\n".join(stderr_lines)),
            "retryable": True,
            "user_actionable": False,
            "summary": "Anthropic reported a rate limit.",
            "next_action": "Retry later. Parity already retries rate limits automatically when possible.",
        }

    candidates = [raw_result, "\n".join(stderr_lines), *stderr_lines]
    for candidate in candidates:
        payload = _extract_anthropic_error_payload(candidate)
        if payload is None:
            continue
        error_type = payload["type"]
        status, category, retryable, user_actionable, next_action = _ANTHROPIC_ERROR_TYPES.get(
            error_type,
            (
                None,
                "provider_error",
                False,
                True,
                "Inspect the Anthropic error message and request ID in the diagnostics artifact, then rerun once the issue is resolved.",
            ),
        )
        return {
            "category": category,
            "provider": "anthropic",
            "http_status": status,
            "provider_error_type": error_type,
            "request_id": payload.get("request_id") or _extract_request_id(candidate),
            "retryable": retryable,
            "user_actionable": user_actionable,
            "summary": payload["message"],
            "next_action": next_action,
        }

    combined = "\n".join(candidate for candidate in candidates if isinstance(candidate, str))
    lowered = combined.lower()
    heuristic_matches = (
        ("out of credits", "billing_error"),
        ("usage credits", "billing_error"),
        ("credit balance", "billing_error"),
        ("invalid api key", "authentication_error"),
        ("api key is invalid", "authentication_error"),
    )
    for needle, error_type in heuristic_matches:
        if needle in lowered:
            status, category, retryable, user_actionable, next_action = _ANTHROPIC_ERROR_TYPES[error_type]
            return {
                "category": category,
                "provider": "anthropic",
                "http_status": status,
                "provider_error_type": error_type,
                "request_id": _extract_request_id(combined),
                "retryable": retryable,
                "user_actionable": user_actionable,
                "summary": combined.strip() or needle,
                "next_action": next_action,
            }

    for error_type, (status, category, retryable, user_actionable, next_action) in _ANTHROPIC_ERROR_TYPES.items():
        if error_type in lowered:
            return {
                "category": category,
                "provider": "anthropic",
                "http_status": status,
                "provider_error_type": error_type,
                "request_id": _extract_request_id(combined),
                "retryable": retryable,
                "user_actionable": user_actionable,
                "summary": combined.strip() or error_type,
                "next_action": next_action,
            }

    return {
        "category": "unknown",
        "provider": None,
        "http_status": None,
        "provider_error_type": None,
        "request_id": _extract_request_id(combined),
        "retryable": False,
        "user_actionable": True,
        "summary": (raw_result or combined or "Unknown stage failure").strip(),
        "next_action": "Inspect the stage diagnostics artifact and Claude CLI debug log, then rerun once the underlying issue is understood.",
    }


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
    assistant_tool_call_counts: dict[str, int] = defaultdict(int)
    previous_tool_uses = 0
    previous_progress_duration_ms = 0
    previous_progress_tool_name: str | None = None
    last_assistant_tool_name: str | None = None
    started_at = datetime.now(tz=timezone.utc).isoformat()
    start_monotonic = time.monotonic()
    timeline: list[dict[str, Any]] = []
    cli_stderr_lines: list[str] = []
    cli_debug_enabled = os.environ.get("PARITY_AGENT_CLI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    inactivity_timeout_s: float | None = None
    raw_inactivity_timeout = os.environ.get("PARITY_AGENT_INACTIVITY_TIMEOUT_S", "").strip()
    if raw_inactivity_timeout:
        try:
            parsed_timeout = float(raw_inactivity_timeout)
        except ValueError:
            parsed_timeout = 0.0
        if parsed_timeout > 0:
            inactivity_timeout_s = parsed_timeout
    schema_summary = None
    output_format = getattr(options, "output_format", None)
    if isinstance(output_format, dict):
        schema_summary = summarize_json_schema(output_format.get("schema"))

    def _event(kind: str, **fields: Any) -> None:
        timeline.append(
            {
                "t_ms": int((time.monotonic() - start_monotonic) * 1000),
                "kind": kind,
                **fields,
            }
        )

    def _stderr_callback(line: str) -> None:
        if len(cli_stderr_lines) < 5000:
            cli_stderr_lines.append(line)
        elif len(cli_stderr_lines) == 5000:
            cli_stderr_lines.append("[truncated] additional Claude CLI stderr lines omitted")

    def _tools_observed_payload(merged_tool_counts: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool_name,
                "count": merged_tool_counts[tool_name],
                "approx_duration_ms": observed_tool_durations_ms.get(tool_name, 0),
            }
            for tool_name in sorted(merged_tool_counts)
        ]

    def _build_diagnostics(
        *,
        completed: bool,
        failure: dict[str, Any] | None = None,
        raw_result_preview: str | None = None,
        merged_tool_counts: dict[str, int] | None = None,
        result_subtype: str | None = None,
    ) -> dict[str, Any]:
        merged_counts = merged_tool_counts or merge_tool_counts(observed_tool_counts, assistant_tool_call_counts)
        diagnostics = {
            "stage": stage_num,
            "started_at": started_at,
            "ended_at": datetime.now(tz=timezone.utc).isoformat(),
            "completed": completed,
            "cli_debug_enabled": cli_debug_enabled,
            "inactivity_timeout_s": inactivity_timeout_s,
            "schema_summary": schema_summary,
            "assistant_messages": assistant_message_count,
            "last_model": last_model,
            "last_assistant_error": last_assistant_error,
            "last_assistant_tool_name": last_assistant_tool_name,
            "result_subtype": result_subtype,
            "observed_tool_uses": sum(merged_counts.values()),
            "tools_observed": _tools_observed_payload(merged_counts),
            "cli_stderr_line_count": len(cli_stderr_lines),
            "cli_stderr_tail": cli_stderr_lines[-40:],
            "timeline": timeline,
        }
        if raw_result_preview:
            diagnostics["raw_result_preview"] = raw_result_preview
        if failure is not None:
            diagnostics["failure"] = failure
        return diagnostics

    existing_extra_args = dict(getattr(options, "extra_args", {}) or {})
    updated_extra_args = {
        **existing_extra_args,
        **({"debug-to-stderr": None} if cli_debug_enabled and "debug-to-stderr" not in existing_extra_args else {}),
    }
    if is_dataclass(options):
        query_options = replace(
            options,
            stderr=_stderr_callback,
            extra_args=updated_extra_args,
        )
    else:
        query_options = copy.copy(options)
        query_options.stderr = _stderr_callback
        query_options.extra_args = updated_extra_args
    print(
        f"[stage-{stage_num}] Agent starting — max_turns={query_options.max_turns} budget=${query_options.max_budget_usd:.2f}",
        file=sys.stderr,
        flush=True,
    )
    _event(
        "start",
        max_turns=query_options.max_turns,
        max_budget_usd=query_options.max_budget_usd,
    )
    if schema_summary is not None:
        _event("schema_summary", **schema_summary)

    message_stream = query(prompt=prompt, options=query_options)
    iterator = message_stream.__aiter__()

    try:
        while True:
            try:
                if inactivity_timeout_s is None:
                    message = await iterator.__anext__()
                else:
                    message = await asyncio.wait_for(iterator.__anext__(), timeout=inactivity_timeout_s)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as exc:
                stall_failure = classify_stage_failure(
                    stderr_lines=cli_stderr_lines,
                    stall_reason=last_assistant_tool_name,
                )
                _event("timeout", stall_reason=last_assistant_tool_name, inactivity_timeout_s=inactivity_timeout_s)
                diagnostics = _build_diagnostics(
                    completed=False,
                    failure=stall_failure,
                    merged_tool_counts=merge_tool_counts(observed_tool_counts, assistant_tool_call_counts),
                )
                aclose = getattr(iterator, "aclose", None)
                if callable(aclose):
                    await aclose()
                raise StageError(
                    "Stage stalled while waiting for Agent SDK output",
                    stage=stage_num,
                    details={
                        "failure": stall_failure,
                        "diagnostics": diagnostics,
                        "debug_log_lines": list(cli_stderr_lines),
                    },
                ) from exc

            if isinstance(message, AssistantMessage):
                assistant_message_count += 1
                last_model = message.model
                if message.error:
                    last_assistant_error = message.error
                    _event("assistant_error", index=assistant_message_count, error=message.error)
                else:
                    tool_names = message_tool_names(message)
                    if tool_names:
                        last_assistant_tool_name = tool_names[-1]
                    for tool_name in tool_names:
                        assistant_tool_call_counts[tool_name] += 1
                    preview = message_text(message)[:120].replace("\n", " ").strip()
                    if preview:
                        _event("assistant_message", index=assistant_message_count, preview=preview)
                        print(
                            f"[stage-{stage_num}] assistant_message {assistant_message_count}: {preview}",
                            file=sys.stderr,
                            flush=True,
                        )
                    else:
                        if tool_names:
                            preview_names = ", ".join(tool_names[:3])
                            suffix = "" if len(tool_names) <= 3 else ", ..."
                            detail = f"tool_calls={preview_names}{suffix}"
                        else:
                            detail = "no_text"
                        _event("assistant_message", index=assistant_message_count, detail=detail)
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
                    _event(
                        "task_progress",
                        last_tool=last_tool_name,
                        cumulative_tool_uses=total_tool_uses,
                        duration_ms=total_duration_ms,
                    )
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
                _event(
                    "result",
                    subtype=message.subtype,
                    is_error=message.is_error,
                    duration_ms=message.duration_ms,
                    num_turns=message.num_turns,
                )
    finally:
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            await aclose()

    if last_assistant_error == "rate_limit":
        failure = classify_stage_failure(
            raw_result=result_message.result if result_message is not None else None,
            stderr_lines=cli_stderr_lines,
            last_assistant_error=last_assistant_error,
        )
        raise RateLimitStageError(
            "rate_limit",
            stage=stage_num,
            retry_count=1,
            details={
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=_safe_preview(result_message.result if result_message is not None else None),
                    result_subtype=result_message.subtype if result_message is not None else None,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )

    if result_message is None:
        failure = classify_stage_failure(stderr_lines=cli_stderr_lines)
        raise StageError(
            "No ResultMessage received from Agent SDK",
            stage=stage_num,
            details={
                "failure": failure,
                "diagnostics": _build_diagnostics(completed=False, failure=failure),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )

    raw_result: Any = (
        result_message.structured_output
        if result_message.structured_output is not None
        else result_message.result
    )
    merged_tool_counts = merge_tool_counts(observed_tool_counts, assistant_tool_call_counts)
    if result_message.subtype == "error_max_budget_usd":
        partial = attempt_partial_extraction(result_message.result)
        failure = classify_stage_failure(
            subtype=result_message.subtype,
            raw_result=result_message.result,
            stderr_lines=cli_stderr_lines,
        )
        raise BudgetExceededError(
            "Cost budget exceeded",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            partial_result=partial,
            details={
                "subtype": result_message.subtype,
                "model": last_model,
                "duration_ms": result_message.duration_ms,
                "num_turns": result_message.num_turns,
                "assistant_messages": assistant_message_count,
                "observed_tool_uses": sum(merged_tool_counts.values()),
                "tools_observed": _tools_observed_payload(merged_tool_counts),
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=_safe_preview(result_message.result),
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )
    if result_message.subtype == "error_max_turns":
        partial = attempt_partial_extraction(result_message.result)
        failure = classify_stage_failure(
            subtype=result_message.subtype,
            raw_result=result_message.result,
            stderr_lines=cli_stderr_lines,
        )
        raise BudgetExceededError(
            "Max turns limit reached — increase max_turns or simplify the stage prompt",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            partial_result=partial,
            details={
                "subtype": result_message.subtype,
                "model": last_model,
                "duration_ms": result_message.duration_ms,
                "num_turns": result_message.num_turns,
                "assistant_messages": assistant_message_count,
                "observed_tool_uses": sum(merged_tool_counts.values()),
                "tools_observed": _tools_observed_payload(merged_tool_counts),
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=_safe_preview(result_message.result),
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )
    if result_message.subtype == "error_max_structured_output_retries":
        truncated = (
            str(result_message.result or "")[:300]
        ).replace("\n", "\\n")
        failure = classify_stage_failure(
            subtype=result_message.subtype,
            raw_result=result_message.result,
            stderr_lines=cli_stderr_lines,
        )
        raise SchemaValidationError(
            f"Stage {stage_num} structured output failed after all retries — "
            f"the agent could not produce JSON matching the required schema. "
            f"Check that the prompt clearly describes all required fields.\n"
            f"Raw response (first 300 chars): {truncated}",
            stage=stage_num,
            details={
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=truncated,
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )
    if result_message.is_error:
        failure = classify_stage_failure(
            subtype=result_message.subtype,
            raw_result=result_message.result,
            stderr_lines=cli_stderr_lines,
            last_assistant_error=last_assistant_error,
        )
        raise StageError(
            result_message.result or "Agent SDK error",
            stage=stage_num,
            cost_usd=result_message.total_cost_usd,
            details={
                "subtype": result_message.subtype,
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=_safe_preview(result_message.result),
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        )

    if result_message.structured_output is None:
        truncated = str(result_message.result or "")[:300].replace("\n", "\\n")
        failure = classify_stage_failure(
            raw_result=result_message.result,
            stderr_lines=cli_stderr_lines,
        )
        raise SchemaValidationError(
            f"Stage {stage_num} structured output was not populated "
            f"(structured_output=None, subtype={result_message.subtype!r}). "
            f"The CLI did not enforce the JSON schema — check schema compatibility with the Agent SDK.\n"
            f"Raw response (first 300 chars): {truncated}",
            stage=stage_num,
            details={
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=truncated,
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
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
        failure = classify_stage_failure(
            subtype="error_max_structured_output_retries",
            raw_result=str(raw_result) if raw_result is not None else None,
            stderr_lines=cli_stderr_lines,
        )
        raise SchemaValidationError(
            f"Stage {stage_num} output failed validation: {exc}\n"
            f"Raw response (first 300 chars): {truncated_result}",
            stage=stage_num,
            details={
                "failure": failure,
                "diagnostics": _build_diagnostics(
                    completed=False,
                    failure=failure,
                    raw_result_preview=truncated_result,
                    merged_tool_counts=merged_tool_counts,
                    result_subtype=result_message.subtype,
                ),
                "debug_log_lines": list(cli_stderr_lines),
            },
        ) from exc

    print(
        f"[stage-{stage_num}] completion: sdk_turns={result_message.num_turns} "
        f"assistant_messages={assistant_message_count} observed_tool_uses={sum(merged_tool_counts.values())}",
        file=sys.stderr,
        flush=True,
    )
    if merged_tool_counts:
        print(
            f"[stage-{stage_num}] tool_summary: {format_tool_summary(merged_tool_counts, observed_tool_durations_ms)}",
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
            "observed_tool_uses": sum(merged_tool_counts.values()),
            "tools_observed": _tools_observed_payload(merged_tool_counts),
            "schema_summary": schema_summary,
            "diagnostics": _build_diagnostics(
                completed=True,
                merged_tool_counts=merged_tool_counts,
                result_subtype=result_message.subtype,
            ),
            "debug_log_lines": list(cli_stderr_lines),
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
    last_rate_limit_error: RateLimitStageError | None = None
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
        except RateLimitStageError as exc:
            last_rate_limit_error = exc
            if attempt >= max_retries - 1:
                raise BudgetExceededError(
                    "Rate limit persisted after retries",
                    stage=stage_num,
                    details=exc.details,
                ) from None
            wait = waits[attempt]
            print(
                f"[stage-{stage_num}] Rate limited on attempt {attempt + 1}/{max_retries}. "
                f"Waiting {wait}s before retry...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(wait)
    raise StageError(
        f"Stage {stage_num} failed after retries",
        stage=stage_num,
        details=last_rate_limit_error.details if last_rate_limit_error is not None else {},
    )


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
        metadata.update(
            {
                key: value
                for key, value in result.extras.items()
                if key not in {"diagnostics", "debug_log_lines"}
            }
        )
    if extra:
        metadata.update(extra)
    return metadata
