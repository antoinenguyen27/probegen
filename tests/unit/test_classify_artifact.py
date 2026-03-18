from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from probegen.cli.get_behavior_diff import (
    _artifact_class,
    _classify_artifact_path,
    _matches_hint_patterns,
    _read_event_payload,
)
from probegen.config import ArtifactDetectionConfig, ProbegenConfig
from probegen.errors import EventPayloadError


def _config(
    *,
    behavior_paths: list[str] | None = None,
    guardrail_paths: list[str] | None = None,
    behavior_exclude: list[str] | None = None,
) -> ProbegenConfig:
    return ProbegenConfig(
        behavior_artifacts=ArtifactDetectionConfig(
            paths=behavior_paths or [],
            exclude=behavior_exclude or [],
        ),
        guardrail_artifacts=ArtifactDetectionConfig(
            paths=guardrail_paths or [],
        ),
    )


# ---------------------------------------------------------------------------
# _classify_artifact_path
# ---------------------------------------------------------------------------


class TestClassifyArtifactPath:
    _cfg = _config()

    def test_judge_keyword(self) -> None:
        assert _classify_artifact_path("prompts/citation_judge.md", self._cfg) == "llm_judge"

    def test_rubric_keyword(self) -> None:
        assert _classify_artifact_path("evals/rubric_v2.md", self._cfg) == "llm_judge"

    def test_tool_description_keyword(self) -> None:
        assert _classify_artifact_path("tools/tool_description.yaml", self._cfg) == "tool_description"

    def test_planner_keyword(self) -> None:
        assert _classify_artifact_path("agents/planner_agent.md", self._cfg) == "planner_prompt"

    def test_retrieval_keyword(self) -> None:
        assert _classify_artifact_path("prompts/retrieval_instruction.md", self._cfg) == "retrieval_instruction"

    def test_output_schema_keyword(self) -> None:
        assert _classify_artifact_path("schemas/output_schema.json", self._cfg) == "output_schema"

    def test_input_classifier_keyword(self) -> None:
        assert _classify_artifact_path("prompts/input_classifier.md", self._cfg) == "input_classifier"

    def test_output_classifier_keyword(self) -> None:
        assert _classify_artifact_path("prompts/output_classifier.md", self._cfg) == "output_classifier"

    def test_safety_keyword(self) -> None:
        assert _classify_artifact_path("prompts/safety_check.md", self._cfg) == "safety_classifier"

    def test_retry_keyword(self) -> None:
        assert _classify_artifact_path("prompts/retry_policy.md", self._cfg) == "retry_policy"

    def test_system_prompt_keyword(self) -> None:
        assert _classify_artifact_path("prompts/citation_agent/system_prompt.md", self._cfg) == "system_prompt"

    def test_instruction_keyword(self) -> None:
        assert _classify_artifact_path("prompts/instruction_set.md", self._cfg) == "system_prompt"

    def test_unknown_fallback(self) -> None:
        assert _classify_artifact_path("src/config.py", self._cfg) == "unknown"

    def test_case_insensitive(self) -> None:
        assert _classify_artifact_path("prompts/SAFETY_RULES.md", self._cfg) == "safety_classifier"

    def test_judge_takes_priority_over_prompt(self) -> None:
        # "judge" appears before "prompt/instruction/system" in the priority chain
        assert _classify_artifact_path("prompts/judge_system_prompt.md", self._cfg) == "llm_judge"


# ---------------------------------------------------------------------------
# _artifact_class
# ---------------------------------------------------------------------------


class TestArtifactClass:
    def test_guardrail_pattern_match(self) -> None:
        cfg = _config(guardrail_paths=["guardrails/**"])
        assert _artifact_class("guardrails/safety_rules.md", cfg) == "guardrail"

    def test_no_patterns_returns_behavior_defining(self) -> None:
        cfg = _config()
        assert _artifact_class("prompts/system_prompt.md", cfg) == "behavior_defining"

    def test_non_matching_path_returns_behavior_defining(self) -> None:
        cfg = _config(guardrail_paths=["guardrails/**"])
        assert _artifact_class("prompts/system_prompt.md", cfg) == "behavior_defining"


# ---------------------------------------------------------------------------
# _matches_hint_patterns
# ---------------------------------------------------------------------------


class TestMatchesHintPatterns:
    def test_behavior_path_matches(self) -> None:
        cfg = _config(behavior_paths=["prompts/**"])
        assert _matches_hint_patterns("prompts/agent/system_prompt.md", cfg) is True

    def test_guardrail_path_matches(self) -> None:
        cfg = _config(guardrail_paths=["guardrails/**"])
        assert _matches_hint_patterns("guardrails/safety.md", cfg) is True

    def test_excluded_path_does_not_match(self) -> None:
        cfg = _config(behavior_paths=["prompts/**"], behavior_exclude=["prompts/drafts/**"])
        assert _matches_hint_patterns("prompts/drafts/wip.md", cfg) is False

    def test_no_patterns_returns_false(self) -> None:
        cfg = _config()
        assert _matches_hint_patterns("prompts/system_prompt.md", cfg) is False

    def test_non_matching_path_returns_false(self) -> None:
        cfg = _config(behavior_paths=["prompts/**"])
        assert _matches_hint_patterns("src/config.py", cfg) is False


# ---------------------------------------------------------------------------
# _read_event_payload
# ---------------------------------------------------------------------------


class TestReadEventPayload:
    def test_raises_when_env_var_missing(self) -> None:
        with pytest.raises(EventPayloadError, match="GITHUB_EVENT_PATH is not set"):
            _read_event_payload({})

    def test_raises_when_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(EventPayloadError, match="Malformed GitHub event payload"):
            _read_event_payload({"GITHUB_EVENT_PATH": str(tmp_path / "nonexistent.json")})

    def test_raises_on_malformed_json(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "event.json"
        bad_file.write_text("not json", encoding="utf-8")
        with pytest.raises(EventPayloadError, match="Malformed GitHub event payload"):
            _read_event_payload({"GITHUB_EVENT_PATH": str(bad_file)})

    def test_raises_when_pull_request_key_missing(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"repository": {"full_name": "org/repo"}}), encoding="utf-8")
        with pytest.raises(EventPayloadError, match="missing pull_request or repository"):
            _read_event_payload({"GITHUB_EVENT_PATH": str(event_file)})

    def test_raises_when_repository_key_missing(self, tmp_path: Path) -> None:
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"pull_request": {"number": 1}}), encoding="utf-8")
        with pytest.raises(EventPayloadError, match="missing pull_request or repository"):
            _read_event_payload({"GITHUB_EVENT_PATH": str(event_file)})

    def test_valid_payload_returns_dict(self, tmp_path: Path) -> None:
        payload = {
            "pull_request": {"number": 42, "title": "Test PR"},
            "repository": {"full_name": "org/repo"},
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload), encoding="utf-8")
        result = _read_event_payload({"GITHUB_EVENT_PATH": str(event_file)})
        assert result["pull_request"]["number"] == 42
        assert result["repository"]["full_name"] == "org/repo"
