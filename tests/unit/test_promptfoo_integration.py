from __future__ import annotations

import json
from pathlib import Path

import yaml

from parity.integrations.promptfoo import (
    PromptfooReader,
    PromptfooWriter,
    probe_to_promptfoo_test,
    promptfoo_assertion_type,
)
from parity.models import ProbeCase

_FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"


def _load_probe(overrides: dict | None = None) -> ProbeCase:
    """Return a minimal ProbeCase for use in assertions."""
    data = {
        "probe_id": "probe_001",
        "gap_id": "gap_001",
        "probe_type": "boundary_probe",
        "is_conversational": False,
        "input": "What year was the Paris Agreement signed?",
        "input_format": "string",
        "expected_behavior": "The agent includes a citation.",
        "expected_behavior_type": "llm_rubric",
        "rubric": "The response cites a source.",
        "probe_rationale": "Tests the intended improvement.",
        "related_risk_flag": "May not cite factual claims",
        "nearest_existing_case_id": "case_001",
        "nearest_existing_similarity": 0.75,
        "specificity_confidence": 0.9,
        "testability_confidence": 0.9,
        "realism_confidence": 0.9,
        "approved": False,
    }
    if overrides:
        data.update(overrides)
    return ProbeCase.model_validate(data)


# ---------------------------------------------------------------------------
# promptfoo_assertion_type
# ---------------------------------------------------------------------------


class TestPromptfooAssertionType:
    def test_exact_output_maps_to_equals(self) -> None:
        assert promptfoo_assertion_type("exact_output") == "equals"

    def test_contains_maps_to_contains(self) -> None:
        assert promptfoo_assertion_type("contains") == "contains"

    def test_not_contains_maps_to_not_hyphen_contains(self) -> None:
        assert promptfoo_assertion_type("not_contains") == "not-contains"

    def test_llm_rubric_maps_to_llm_hyphen_rubric(self) -> None:
        assert promptfoo_assertion_type("llm_rubric") == "llm-rubric"

    def test_format_check_maps_to_javascript(self) -> None:
        assert promptfoo_assertion_type("format_check") == "javascript"

    def test_unknown_type_replaces_underscores_with_hyphens(self) -> None:
        assert promptfoo_assertion_type("custom_check_type") == "custom-check-type"


# ---------------------------------------------------------------------------
# probe_to_promptfoo_test
# ---------------------------------------------------------------------------


class TestProbeToPromptfooTest:
    def test_string_input_uses_query_var(self) -> None:
        probe = _load_probe()
        result = probe_to_promptfoo_test(probe)
        assert result["vars"] == {"query": "What year was the Paris Agreement signed?"}

    def test_assertion_type_derived_from_expected_behavior_type(self) -> None:
        probe = _load_probe()
        result = probe_to_promptfoo_test(probe)
        assert result["assert"][0]["type"] == "llm-rubric"

    def test_rubric_used_as_assertion_value_when_present(self) -> None:
        probe = _load_probe()
        result = probe_to_promptfoo_test(probe)
        assert result["assert"][0]["value"] == "The response cites a source."

    def test_expected_behavior_used_when_no_rubric(self) -> None:
        probe = _load_probe({"rubric": None})
        result = probe_to_promptfoo_test(probe)
        assert result["assert"][0]["value"] == "The agent includes a citation."

    def test_description_contains_probe_id_and_type(self) -> None:
        probe = _load_probe()
        result = probe_to_promptfoo_test(probe)
        assert "probe_001" in result["description"]
        assert "boundary_probe" in result["description"]

    def test_conversation_input_uses_messages_var(self) -> None:
        probe = _load_probe(
            {
                "is_conversational": True,
                "input": [{"role": "user", "content": "Hello"}],
                "input_format": "conversation",
            }
        )
        result = probe_to_promptfoo_test(probe)
        assert "messages" in result["vars"]
        assert result["vars"]["messages"] == [{"role": "user", "content": "Hello"}]


# ---------------------------------------------------------------------------
# PromptfooWriter
# ---------------------------------------------------------------------------


class TestPromptfooWriter:
    def test_creates_yaml_file(self, tmp_path: Path) -> None:
        probe = _load_probe()
        writer = PromptfooWriter()
        outputs = writer.write_tests([probe], test_file=tmp_path / "probes.yaml")
        assert outputs["test_file"].exists()

    def test_written_yaml_contains_test(self, tmp_path: Path) -> None:
        probe = _load_probe()
        writer = PromptfooWriter()
        output_path = tmp_path / "probes.yaml"
        writer.write_tests([probe], test_file=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert len(data["tests"]) == 1
        assert data["tests"][0]["vars"] == {"query": "What year was the Paris Agreement signed?"}

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        probe1 = _load_probe()
        probe2 = _load_probe({"probe_id": "probe_002"})
        writer = PromptfooWriter()
        output_path = tmp_path / "probes.yaml"
        writer.write_tests([probe1], test_file=output_path)
        writer.write_tests([probe2], test_file=output_path)
        data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert len(data["tests"]) == 2

    def test_conversational_probe_creates_prompt_file(self, tmp_path: Path) -> None:
        probe = _load_probe(
            {
                "is_conversational": True,
                "input": [{"role": "user", "content": "Hi"}],
                "input_format": "conversation",
            }
        )
        writer = PromptfooWriter()
        outputs = writer.write_tests([probe], test_file=tmp_path / "probes.yaml")
        assert "prompt_file" in outputs
        assert outputs["prompt_file"].exists()

    def test_non_conversational_probe_has_no_prompt_file(self, tmp_path: Path) -> None:
        probe = _load_probe()
        writer = PromptfooWriter()
        outputs = writer.write_tests([probe], test_file=tmp_path / "probes.yaml")
        assert "prompt_file" not in outputs


# ---------------------------------------------------------------------------
# PromptfooReader
# ---------------------------------------------------------------------------


class TestPromptfooReader:
    def _write_yaml(self, tmp_path: Path, tests: list[dict]) -> Path:
        path = tmp_path / "promptfooconfig.yaml"
        path.write_text(yaml.safe_dump({"tests": tests}), encoding="utf-8")
        return path

    def test_reads_string_query_test(self, tmp_path: Path) -> None:
        path = self._write_yaml(
            tmp_path,
            [
                {
                    "vars": {"query": "What is 2+2?"},
                    "assert": [{"type": "equals", "value": "4"}],
                }
            ],
        )
        reader = PromptfooReader()
        cases = reader.fetch_examples(path)
        assert len(cases) == 1
        assert cases[0].input_normalized == "What is 2+2?"

    def test_reads_expected_output(self, tmp_path: Path) -> None:
        path = self._write_yaml(
            tmp_path,
            [
                {
                    "vars": {"query": "Hello?"},
                    "assert": [{"type": "contains", "value": "world"}],
                }
            ],
        )
        reader = PromptfooReader()
        cases = reader.fetch_examples(path)
        assert cases[0].expected_output == "world"

    def test_empty_yaml_returns_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        reader = PromptfooReader()
        assert reader.fetch_examples(path) == []

    def test_source_platform_is_promptfoo(self, tmp_path: Path) -> None:
        path = self._write_yaml(tmp_path, [{"vars": {"query": "Q"}}])
        reader = PromptfooReader()
        cases = reader.fetch_examples(path)
        assert cases[0].source_platform == "promptfoo"
