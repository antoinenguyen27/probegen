from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from parity.integrations.langsmith import LangSmithReader, LangSmithWriter
from parity.models import EvalProposalManifest

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_rendering(overrides: dict | None = None):
    proposal = EvalProposalManifest.model_validate(
        json.loads((_FIXTURES / "sample_proposal.json").read_text(encoding="utf-8"))
    )
    rendering = proposal.renderings[1].model_copy(deep=True)
    rendering.rendering_kind = "langsmith_example"
    rendering.payload = {
        "inputs": {"query": "What year was the Paris Agreement signed?"},
        "outputs": {"answer": "The answer states 2015 and cites a source."},
        "metadata": {
            "method_kind": "hybrid",
            "parity_assertions": [
                {
                    "assertion_id": "intent_002:deterministic",
                    "assertion_kind": "deterministic",
                    "operator": "contains",
                    "expected_value": "The answer states 2015 and cites a source.",
                    "metadata": {"output_binding": "answer"},
                },
                {
                    "assertion_id": "intent_002:judge",
                    "assertion_kind": "judge",
                    "operator": "llm-rubric",
                    "rubric": "The answer states 2015 and cites a source.",
                    "metadata": {"output_binding": "answer"},
                },
            ],
            "parity_output_binding": "answer",
        },
        "tags": ["expected_improvement", "parity"],
    }
    if overrides:
        for key, value in overrides.items():
            if key in {"payload", "renderer_id", "rendering_id", "rendering_kind"}:
                setattr(rendering, key, value)
    return rendering


class TestLangSmithWriter:
    def test_create_examples_uses_stable_ids_from_rendering_content(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="00000000-0000-0000-0000-000000000123")
        writer = LangSmithWriter(client=mock_client)

        baseline = _load_rendering()
        same_semantics = _load_rendering(
            {
                "rendering_id": "render-intent_999",
                "renderer_id": "promptfoo/native",
            }
        )

        writer.create_examples_from_renderings([baseline], dataset_name="demo-dataset", source_pr=1, source_commit="abc123")
        writer.create_examples_from_renderings([same_semantics], dataset_name="demo-dataset", source_pr=2, source_commit="def456")

        first_call = mock_client.create_examples.call_args_list[0].kwargs
        second_call = mock_client.create_examples.call_args_list[1].kwargs
        first_example = first_call["examples"][0]
        second_example = second_call["examples"][0]

        assert isinstance(first_example["id"], UUID)
        assert first_example["id"] == second_example["id"]
        assert first_example["metadata"]["rendering_id"] == "render-intent_002"
        assert second_example["metadata"]["rendering_id"] == "render-intent_999"
        assert first_example["outputs"] == {"answer": "The answer states 2015 and cites a source."}
        assert first_example["metadata"]["tags"] == ["expected_improvement", "parity"]

    def test_create_examples_prefers_dataset_id_when_available(self) -> None:
        mock_client = Mock()
        writer = LangSmithWriter(client=mock_client)

        writer.create_examples_from_renderings(
            [_load_rendering()],
            dataset_name="demo-dataset",
            dataset_id="dataset-123",
            source_pr=1,
            source_commit="abc123",
        )

        mock_client.read_dataset.assert_not_called()
        assert mock_client.create_examples.call_args.kwargs["dataset_id"] == "dataset-123"


class TestLangSmithReader:
    def test_fetch_examples_prefers_structured_parity_assertions(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="dataset-123", name="demo-dataset")
        mock_client.list_examples.return_value = [
            SimpleNamespace(
                id="example-1",
                inputs={"query": "What year was the Paris Agreement signed?"},
                outputs={"answer": "2015"},
                metadata={
                    "tags": ["citation"],
                    "parity_assertions": [
                        {
                            "assertion_id": "example-1:0",
                            "assertion_kind": "deterministic",
                            "operator": "equals",
                            "expected_value": "2015",
                            "metadata": {"output_binding": "answer"},
                        },
                        {
                            "assertion_id": "example-1:1",
                            "assertion_kind": "judge",
                            "operator": "llm-rubric",
                            "rubric": "The answer includes a citation.",
                            "metadata": {"output_binding": "answer"},
                        },
                    ],
                },
            )
        ]
        reader = LangSmithReader(client=mock_client)

        cases = reader.fetch_examples(dataset_name="demo-dataset")

        assert len(cases) == 1
        assert cases[0].method_kind == "hybrid"
        assert cases[0].native_output == {"answer": "2015"}
        assert len(cases[0].native_assertions) == 2
        assert cases[0].tags == ["citation"]

    def test_fetch_examples_prefers_dataset_id_when_available(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="dataset-123", name="demo-dataset")
        mock_client.list_examples.return_value = []
        reader = LangSmithReader(client=mock_client)

        reader.fetch_examples(dataset_name="demo-dataset", dataset_id="dataset-123")

        assert mock_client.read_dataset.call_args.kwargs == {"dataset_id": "dataset-123"}

    def test_discover_evaluator_bindings_uses_feedback_formulas_and_configs(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="dataset-123", name="demo-dataset")
        mock_client.list_feedback_formulas.side_effect = [
            [
                SimpleNamespace(
                    id="formula-1",
                    feedback_key="helpfulness",
                    aggregation_type="avg",
                    formula_parts=[SimpleNamespace(key="helpfulness", weight=1.0)],
                )
            ],
            [],
        ]
        mock_client.list_projects.return_value = []
        mock_client.list_feedback_configs.return_value = [
            SimpleNamespace(
                feedback_key="helpfulness",
                feedback_config={"type": "continuous", "min": 0.0, "max": 1.0},
            )
        ]
        reader = LangSmithReader(client=mock_client)

        bindings = reader.discover_evaluator_bindings(dataset_name="demo-dataset")

        assert bindings[0].discovery_mode == "formal"
        assert bindings[0].binding_status == "attached"
        assert bindings[0].verification_status == "verified"
        assert bindings[0].binding_id == "langsmith::dataset_formula::helpfulness"
        assert any(binding.binding_id == "langsmith::feedback_config::helpfulness" for binding in bindings)

    def test_discover_evaluator_bindings_prefers_dataset_id_when_available(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="dataset-123", name="demo-dataset")
        mock_client.list_feedback_formulas.side_effect = [[], []]
        mock_client.list_projects.return_value = []
        mock_client.list_feedback_configs.return_value = []
        reader = LangSmithReader(client=mock_client)

        reader.discover_evaluator_bindings(dataset_name="demo-dataset", dataset_id="dataset-123")

        assert mock_client.read_dataset.call_args.kwargs == {"dataset_id": "dataset-123"}
