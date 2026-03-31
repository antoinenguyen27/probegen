from __future__ import annotations

import asyncio
import json
from pathlib import Path

from claude_agent_sdk import Transport, query
from claude_agent_sdk.types import ResultMessage

from parity.stages.security import (
    build_stage1_options,
    build_stage2_options,
    build_stage3_options,
    evaluate_mcp_tool_request,
    evaluate_stage1_tool_request,
)


class FakeTransport(Transport):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._ready = False

    async def connect(self) -> None:
        self._ready = True

    async def write(self, data: str) -> None:
        message = json.loads(data)
        if message.get("type") == "control_request":
            await self._queue.put(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": message["request_id"],
                        "response": {},
                    },
                }
            )
        elif message.get("type") == "user":
            await self._queue.put(
                {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 0,
                    "duration_api_ms": 0,
                    "is_error": False,
                    "num_turns": 0,
                    "session_id": "test-session",
                    "stop_reason": "end_turn",
                    "total_cost_usd": 0.0,
                    "usage": {},
                    "result": "{}",
                    "structured_output": {},
                }
            )

    async def close(self) -> None:
        self._ready = False
        await self._queue.put(None)

    def is_ready(self) -> bool:
        return self._ready

    async def end_input(self) -> None:
        await self._queue.put(None)

    async def _iter_messages(self):
        while True:
            message = await self._queue.get()
            if message is None:
                break
            yield message

    def read_messages(self):
        return self._iter_messages()


def test_stage1_policy_allows_read_only_git_commands(tmp_path: Path) -> None:
    result = evaluate_stage1_tool_request(
        tool_name="Bash",
        tool_input={"command": "git show origin/main:app/router.py"},
        repo_root=tmp_path,
    )
    assert result.behavior == "allow"

    result = evaluate_stage1_tool_request(
        tool_name="Bash",
        tool_input={"command": "git diff --unified=5 origin/main...HEAD -- app/router.py"},
        repo_root=tmp_path,
    )
    assert result.behavior == "allow"


def test_stage1_policy_denies_broad_shell_commands(tmp_path: Path) -> None:
    result = evaluate_stage1_tool_request(
        tool_name="Bash",
        tool_input={"command": "env"},
        repo_root=tmp_path,
    )
    assert result.behavior == "deny"

    result = evaluate_stage1_tool_request(
        tool_name="Bash",
        tool_input={"command": "git show origin/main:app/router.py | cat"},
        repo_root=tmp_path,
    )
    assert result.behavior == "deny"


def test_stage1_policy_denies_sensitive_git_inspection_paths(tmp_path: Path) -> None:
    result = evaluate_stage1_tool_request(
        tool_name="Bash",
        tool_input={"command": "git show origin/main:.env"},
        repo_root=tmp_path,
    )
    assert result.behavior == "deny"


def test_stage1_policy_denies_sensitive_file_reads(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")

    result = evaluate_stage1_tool_request(
        tool_name="Read",
        tool_input={"file_path": ".env"},
        repo_root=tmp_path,
    )
    assert result.behavior == "deny"


def test_stage1_policy_allows_repo_file_reads(tmp_path: Path) -> None:
    path = tmp_path / "app" / "router.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ROUTER = 'ok'", encoding="utf-8")

    result = evaluate_stage1_tool_request(
        tool_name="Read",
        tool_input={"file_path": "app/router.py"},
        repo_root=tmp_path,
    )
    assert result.behavior == "allow"


def test_stage1_policy_allows_non_secret_env_templates(tmp_path: Path) -> None:
    path = tmp_path / ".env.example"
    path.write_text("OPENAI_API_KEY=", encoding="utf-8")

    result = evaluate_stage1_tool_request(
        tool_name="Read",
        tool_input={"file_path": ".env.example"},
        repo_root=tmp_path,
    )
    assert result.behavior == "allow"


def test_stage1_policy_allows_absolute_glob_paths_within_repo(tmp_path: Path) -> None:
    result = evaluate_stage1_tool_request(
        tool_name="Glob",
        tool_input={"path": str(tmp_path), "pattern": "**/*.py"},
        repo_root=tmp_path,
    )
    assert result.behavior == "allow"


def test_stage1_policy_denies_paths_outside_repo(tmp_path: Path) -> None:
    result = evaluate_stage1_tool_request(
        tool_name="Read",
        tool_input={"file_path": "../outside.txt"},
        repo_root=tmp_path,
    )
    assert result.behavior == "deny"


def test_stage1_options_use_narrow_tool_set(tmp_path: Path) -> None:
    options = build_stage1_options(
        cwd=tmp_path,
        max_turns=20,
        max_budget_usd=0.5,
        output_schema={"type": "object", "properties": {}},
    )

    assert options.tools == ["Read", "Glob", "Bash"]
    assert options.can_use_tool is None
    assert options.hooks is not None
    assert "PreToolUse" in options.hooks
    assert options.hooks["PreToolUse"][0].matcher == "Read|Glob|Bash"


def test_stage1_options_support_string_prompt_queries(tmp_path: Path) -> None:
    options = build_stage1_options(
        cwd=tmp_path,
        max_turns=20,
        max_budget_usd=0.5,
        output_schema={"type": "object", "properties": {}},
    )
    transport = FakeTransport()

    async def run_query() -> list[ResultMessage]:
        messages: list[ResultMessage] = []
        async for message in query(prompt="hello", options=options, transport=transport):
            if isinstance(message, ResultMessage):
                messages.append(message)
        return messages

    results = asyncio.run(run_query())
    assert len(results) == 1


def test_stage_mcp_policy_allows_expected_stage2_prefix() -> None:
    result = evaluate_mcp_tool_request(
        tool_name="mcp__parity_stage2__fetch_eval_target_snapshot",
        allowed_tool_names=("mcp__parity_stage2__fetch_eval_target_snapshot",),
    )

    assert result.behavior == "allow"


def test_stage_mcp_policy_denies_other_mcp_prefixes() -> None:
    result = evaluate_mcp_tool_request(
        tool_name="mcp__parity_stage3__read_target_profile",
        allowed_tool_names=("mcp__parity_stage2__fetch_eval_target_snapshot",),
    )

    assert result.behavior == "deny"


def test_stage2_options_allow_only_host_owned_mcp_tools(tmp_path: Path) -> None:
    options = build_stage2_options(
        cwd=tmp_path,
        max_turns=10,
        max_budget_usd=0.25,
        output_schema={"type": "object", "properties": {}},
        mcp_servers={"parity_stage2": {"type": "sdk", "name": "parity-stage2", "instance": object()}},
    )

    assert options.tools == []
    assert options.hooks is not None
    assert "PreToolUse" in options.hooks
    assert options.hooks["PreToolUse"][0].matcher is not None
    assert "mcp__parity_stage2__fetch_eval_target_snapshot" in options.hooks["PreToolUse"][0].matcher
    assert "StructuredOutput" not in options.hooks["PreToolUse"][0].matcher
    assert "parity_stage2" in options.mcp_servers


def test_stage3_options_disable_builtin_tools(tmp_path: Path) -> None:
    options = build_stage3_options(
        cwd=tmp_path,
        max_turns=10,
        max_budget_usd=0.25,
        output_schema={"type": "object", "properties": {}},
    )

    assert options.tools == []
    assert options.mcp_servers == {}
    assert options.hooks is not None
    assert "PreToolUse" in options.hooks
    assert options.hooks["PreToolUse"][0].matcher is not None
    assert "mcp__parity_stage3__list_targets" in options.hooks["PreToolUse"][0].matcher
