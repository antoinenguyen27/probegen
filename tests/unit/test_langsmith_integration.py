from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from parity.integrations.langsmith import LangSmithWriter
from parity.models import ProbeCase


def _load_probe(overrides: dict | None = None) -> ProbeCase:
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


class TestLangSmithWriter:
    def test_create_examples_uses_per_example_ids_with_stable_content_identity(self) -> None:
        mock_client = Mock()
        mock_client.read_dataset.return_value = SimpleNamespace(id="00000000-0000-0000-0000-000000000123")
        writer = LangSmithWriter(client=mock_client)

        baseline = _load_probe()
        same_semantics = _load_probe(
            {
                "probe_id": "probe_999",
                "probe_type": "expected_improvement",
                "probe_rationale": "Different explanation for the same test.",
                "related_risk_flag": "Different wording of the same risk.",
                "nearest_existing_case_id": "case_999",
                "nearest_existing_similarity": 0.12,
                "specificity_confidence": 0.42,
                "testability_confidence": 0.43,
                "realism_confidence": 0.44,
            }
        )

        writer.create_examples([baseline], dataset_name="demo-dataset", source_pr=1, source_commit="abc123")
        writer.create_examples([same_semantics], dataset_name="demo-dataset", source_pr=2, source_commit="def456")

        first_call = mock_client.create_examples.call_args_list[0].kwargs
        second_call = mock_client.create_examples.call_args_list[1].kwargs

        assert first_call["dataset_id"] == "00000000-0000-0000-0000-000000000123"
        assert second_call["dataset_id"] == "00000000-0000-0000-0000-000000000123"
        assert "ids" not in first_call
        assert "ids" not in second_call

        first_example = first_call["examples"][0]
        second_example = second_call["examples"][0]

        assert isinstance(first_example["id"], UUID)
        assert first_example["id"] == second_example["id"]
        assert first_example["metadata"]["probe_id"] == "probe_001"
        assert second_example["metadata"]["probe_id"] == "probe_999"
