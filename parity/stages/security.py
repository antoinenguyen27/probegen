from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import HookContext, HookMatcher, PreToolUseHookInput, SyncHookJSONOutput

_SENSITIVE_PATH_PATTERNS = (
    ".env",
    ".env.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".claude/**",
    ".git/**",
    ".parity/**",
    "secrets/**",
    "**/secrets/**",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/*credentials*",
)
_NON_SECRET_ENV_TEMPLATES = {".env.example", ".env.sample"}

_BASH_META_TOKENS = ("|", ";", "&", ">", "<", "$", "`", "\n", "\r", "(", ")", "{", "}")
_ALLOWED_STAGE1_BASH_PATTERNS = (
    re.compile(r"^git show origin/[^\s:]+:[^\n\r]+$"),
    re.compile(r"^git diff --unified=\d+ origin/[^\s]+\.{3}HEAD -- [^\n\r]+$"),
    re.compile(r"^git ls-files(?: [^\n\r]+)?$"),
)
_STAGE1_TOOL_NAMES = ("Read", "Glob", "Bash")
_STAGE2_MCP_TOOL_NAMES = (
    "mcp__parity_stage2__discover_eval_targets",
    "mcp__parity_stage2__fetch_eval_target_snapshot",
    "mcp__parity_stage2__discover_target_evaluators",
    "mcp__parity_stage2__read_evaluator_binding",
    "mcp__parity_stage2__verify_evaluator_binding",
    "mcp__parity_stage2__discover_repo_eval_assets",
    "mcp__parity_stage2__read_repo_eval_asset",
    "mcp__parity_stage2__list_platform_evaluator_capabilities",
    "mcp__parity_stage2__embed_batch",
    "mcp__parity_stage2__find_similar",
    "mcp__parity_stage2__find_similar_batch",
)
_STAGE3_MCP_TOOL_NAMES = (
    "mcp__parity_stage3__list_gap_dossiers",
    "mcp__parity_stage3__read_gap_dossier",
    "mcp__parity_stage3__list_targets",
    "mcp__parity_stage3__read_target_profile",
    "mcp__parity_stage3__list_evaluator_dossiers",
    "mcp__parity_stage3__read_evaluator_dossier",
    "mcp__parity_stage3__read_target_samples",
    "mcp__parity_stage3__read_case_snapshot",
    "mcp__parity_stage3__read_repo_eval_asset_excerpt",
)


@dataclass(frozen=True, slots=True)
class Stage1ToolDecision:
    behavior: Literal["allow", "deny"]
    message: str | None = None


def _build_tool_matcher(tool_names: tuple[str, ...]) -> str:
    return "|".join(tool_names)


def build_stage1_options(
    *,
    cwd: str | Path,
    max_turns: int,
    max_budget_usd: float,
    output_schema: dict[str, Any],
) -> ClaudeAgentOptions:
    repo_root = Path(cwd).resolve()
    return ClaudeAgentOptions(
        tools=list(_STAGE1_TOOL_NAMES),
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=_build_tool_matcher(_STAGE1_TOOL_NAMES),
                    hooks=[build_stage1_pre_tool_use_hook(repo_root)],
                )
            ]
        },
        mcp_servers={},
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        cwd=str(repo_root),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
    )


def build_stage1_pre_tool_use_hook(repo_root: Path):
    repo_root = repo_root.resolve()

    async def pre_tool_use(
        input_data: PreToolUseHookInput,
        _tool_use_id: str | None,
        _context: HookContext,
    ) -> SyncHookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        decision = evaluate_stage1_tool_request(
            tool_name=tool_name,
            tool_input=tool_input,
            repo_root=repo_root,
        )
        return _pre_tool_use_response(decision)

    return pre_tool_use


def build_stage2_options(
    *,
    cwd: str | Path,
    max_turns: int,
    max_budget_usd: float,
    output_schema: dict[str, Any],
    mcp_servers: dict[str, Any] | None = None,
) -> ClaudeAgentOptions:
    return _build_mcp_stage_options(
        cwd=cwd,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_schema=output_schema,
        mcp_servers=mcp_servers,
        allowed_tool_names=_STAGE2_MCP_TOOL_NAMES,
    )


def build_stage3_options(
    *,
    cwd: str | Path,
    max_turns: int,
    max_budget_usd: float,
    output_schema: dict[str, Any],
    mcp_servers: dict[str, Any] | None = None,
) -> ClaudeAgentOptions:
    return _build_mcp_stage_options(
        cwd=cwd,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_schema=output_schema,
        mcp_servers=mcp_servers,
        allowed_tool_names=_STAGE3_MCP_TOOL_NAMES,
    )


def build_mcp_pre_tool_use_hook(*, allowed_tool_names: tuple[str, ...]):
    async def pre_tool_use(
        input_data: PreToolUseHookInput,
        _tool_use_id: str | None,
        _context: HookContext,
    ) -> SyncHookJSONOutput:
        tool_name = input_data.get("tool_name", "")
        decision = evaluate_mcp_tool_request(
            tool_name=tool_name,
            allowed_tool_names=allowed_tool_names,
        )
        return _pre_tool_use_response(decision)

    return pre_tool_use


def _build_mcp_stage_options(
    *,
    cwd: str | Path,
    max_turns: int,
    max_budget_usd: float,
    output_schema: dict[str, Any],
    mcp_servers: dict[str, Any] | None,
    allowed_tool_names: tuple[str, ...],
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        tools=[],
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher=_build_tool_matcher(allowed_tool_names),
                    hooks=[build_mcp_pre_tool_use_hook(allowed_tool_names=allowed_tool_names)],
                )
            ]
        },
        mcp_servers=mcp_servers or {},
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        cwd=str(Path(cwd).resolve()),
        output_format={
            "type": "json_schema",
            "schema": output_schema,
        },
    )


def evaluate_stage1_tool_request(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    repo_root: Path,
) -> Stage1ToolDecision:
    repo_root = repo_root.resolve()
    if tool_name == "Bash":
        command = _extract_command(tool_input)
        if not command:
            return Stage1ToolDecision("deny", "Stage 1 only permits specific read-only git commands.")
        if any(token in command for token in _BASH_META_TOKENS):
            return Stage1ToolDecision("deny", "Stage 1 Bash is restricted to simple read-only git commands.")
        if not any(pattern.fullmatch(command) for pattern in _ALLOWED_STAGE1_BASH_PATTERNS):
            return Stage1ToolDecision(
                "deny",
                (
                    "Stage 1 Bash is restricted to read-only git inspection commands: "
                    "`git show`, `git diff --unified=...`, and `git ls-files`."
                ),
            )
        if _bash_targets_sensitive_path(command):
            return Stage1ToolDecision("deny", "Stage 1 cannot inspect secret-bearing or generated paths.")
        return Stage1ToolDecision("allow")

    if tool_name == "Read":
        for candidate in _extract_string_values(tool_input):
            resolved = _resolve_candidate_path(candidate, repo_root)
            if resolved is None:
                return Stage1ToolDecision("deny", "Stage 1 file reads must stay within the repository.")
            if _matches_sensitive_path(resolved.relative_to(repo_root).as_posix()):
                return Stage1ToolDecision("deny", "Stage 1 cannot read secret-bearing or generated files.")
        return Stage1ToolDecision("allow")

    if tool_name == "Glob":
        for key in ("path", "cwd", "directory"):
            candidate = tool_input.get(key)
            if not isinstance(candidate, str):
                continue
            resolved = _resolve_candidate_path(candidate, repo_root)
            if resolved is None:
                return Stage1ToolDecision("deny", "Stage 1 glob paths must stay within the repository.")
            if _matches_sensitive_path(resolved.relative_to(repo_root).as_posix()):
                return Stage1ToolDecision("deny", "Stage 1 cannot glob secret-bearing or generated paths.")

        for key in ("pattern", "glob"):
            candidate = tool_input.get(key)
            if not isinstance(candidate, str):
                continue
            normalized = candidate.strip()
            if not normalized:
                continue
            if normalized.startswith("/") or normalized.startswith(".."):
                return Stage1ToolDecision("deny", "Stage 1 glob patterns must stay within the repository.")
            if _targets_sensitive_pattern(normalized):
                return Stage1ToolDecision("deny", "Stage 1 cannot glob secret-bearing or generated paths.")
        return Stage1ToolDecision("allow")

    return Stage1ToolDecision("deny", f"Stage 1 tool `{tool_name}` is not permitted.")


def evaluate_mcp_tool_request(
    *,
    tool_name: str,
    allowed_tool_names: tuple[str, ...],
) -> Stage1ToolDecision:
    normalized_tool_name = tool_name.strip()
    if normalized_tool_name in allowed_tool_names:
        return Stage1ToolDecision("allow")

    if normalized_tool_name.startswith("mcp__"):
        allowed = ", ".join(f"`{name}`" for name in allowed_tool_names)
        return Stage1ToolDecision(
            "deny",
            f"This stage only permits host-owned MCP tools: {allowed}.",
        )

    return Stage1ToolDecision("deny", "This stage only permits host-owned MCP tools.")


def _pre_tool_use_response(decision: Stage1ToolDecision) -> SyncHookJSONOutput:
    hook_specific_output: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision.behavior,
    }
    if decision.message:
        hook_specific_output["permissionDecisionReason"] = decision.message

    response: SyncHookJSONOutput = {
        "continue_": True,
        "hookSpecificOutput": hook_specific_output,
    }
    if decision.message:
        response["reason"] = decision.message
    return response


def _extract_command(tool_input: dict[str, Any]) -> str:
    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _extract_string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            values.extend(_extract_string_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_extract_string_values(item))
    return values


def _resolve_candidate_path(candidate: str, repo_root: Path) -> Path | None:
    try:
        path = Path(candidate)
        resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    except Exception:
        return None
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return None
    return resolved


def _matches_sensitive_path(relative_path: str) -> bool:
    if Path(relative_path).name in _NON_SECRET_ENV_TEMPLATES:
        return False
    return any(Path(relative_path).match(pattern) for pattern in _SENSITIVE_PATH_PATTERNS)


def _targets_sensitive_pattern(pattern: str) -> bool:
    normalized = pattern.lstrip("./")
    if not normalized:
        return False
    return any(
        normalized.startswith(prefix)
        for prefix in (".env", ".claude", ".git", ".parity")
    )


def _bash_targets_sensitive_path(command: str) -> bool:
    if command.startswith("git show origin/"):
        _, _, repo_path = command.partition(":")
        return _matches_sensitive_path(repo_path.strip())

    if " -- " in command:
        _, _, repo_path = command.partition(" -- ")
        return _matches_sensitive_path(repo_path.strip())

    if command.startswith("git ls-files "):
        repo_path = command.removeprefix("git ls-files ").strip()
        if repo_path:
            return _targets_sensitive_pattern(repo_path)

    return False
