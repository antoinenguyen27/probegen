from __future__ import annotations

import json
from pathlib import Path

import yaml

from parity.integrations.promptfoo import PromptfooReader, PromptfooWriter, rendering_to_promptfoo_test
from parity.models import EvalProposalManifest, NativeEvalRendering

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_rendering(rendering_index: int = 0) -> NativeEvalRendering:
    proposal = EvalProposalManifest.model_validate(
        json.loads((_FIXTURES / "sample_proposal.json").read_text(encoding="utf-8"))
    )
    return proposal.renderings[rendering_index]


def test_rendering_to_promptfoo_test_preserves_multiple_assertions() -> None:
    rendering = _load_rendering()
    test_case = rendering_to_promptfoo_test(rendering)

    assert test_case["id"] == "intent_001"
    assert len(test_case["assert"]) == 2
    assert test_case["assert"][1]["type"] == "llm-rubric"


def test_promptfoo_writer_creates_yaml_file(tmp_path: Path) -> None:
    writer = PromptfooWriter()
    outputs = writer.write_renderings([_load_rendering()], test_file=tmp_path / "probes.yaml")
    assert outputs["test_file"].exists()


def test_promptfoo_writer_appends_to_existing_file(tmp_path: Path) -> None:
    writer = PromptfooWriter()
    output_path = tmp_path / "probes.yaml"
    writer.write_renderings([_load_rendering(0)], test_file=output_path)
    writer.write_renderings([_load_rendering(1)], test_file=output_path)
    data = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert len(data["tests"]) == 2


def test_promptfoo_writer_conversational_rendering_creates_prompt_file(tmp_path: Path) -> None:
    writer = PromptfooWriter()
    outputs = writer.write_renderings([_load_rendering(0)], test_file=tmp_path / "probes.yaml")
    assert "prompt_file" in outputs
    assert outputs["prompt_file"].exists()


def test_promptfoo_reader_reads_method_kind_and_multiple_assertions(tmp_path: Path) -> None:
    path = tmp_path / "promptfooconfig.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "tests": [
                    {
                        "id": "case_1",
                        "vars": {"query": "What is 2+2?"},
                        "assert": [
                            {"type": "equals", "value": "4"},
                            {"type": "llm-rubric", "value": "The answer is concise."},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reader = PromptfooReader()
    cases = reader.fetch_examples(path)

    assert len(cases) == 1
    assert cases[0].method_kind == "hybrid"
    assert len(cases[0].native_assertions) == 2
    assert cases[0].normalized_projection.input_text == "What is 2+2?"


def test_promptfoo_reader_treats_icontains_as_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "promptfooconfig.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "tests": [
                    {
                        "id": "case_1",
                        "vars": {"query": "What is 2+2?"},
                        "assert": [
                            {"type": "icontains", "value": "4"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reader = PromptfooReader()
    cases = reader.fetch_examples(path)

    assert len(cases) == 1
    assert cases[0].method_kind == "deterministic"
    assert cases[0].native_assertions[0].assertion_kind == "deterministic"
