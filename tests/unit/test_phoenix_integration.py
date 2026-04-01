from __future__ import annotations

from unittest.mock import Mock

from parity.integrations.phoenix import PhoenixWriter
from parity.models import NativeEvalRendering


def _phoenix_rendering() -> NativeEvalRendering:
    return NativeEvalRendering.model_validate(
        {
            "rendering_id": "render-intent_001",
            "intent_id": "intent_001",
            "target_id": "phoenix::demo",
            "method_kind": "judge",
            "rendering_kind": "phoenix_example",
            "renderer_id": "phoenix/dataset-example",
            "write_status": "native_ready",
            "render_confidence": 0.95,
            "target_locator": "demo-dataset",
            "payload": {
                "inputs": {"question": "What year was the Paris Agreement signed?"},
                "outputs": {"answer": "2015"},
                "metadata": {"method_kind": "judge"},
                "tags": ["parity"],
            },
            "summary": "Phoenix-native dataset example payload ready for deterministic writeback.",
        }
    )


def test_phoenix_writer_appends_to_existing_dataset() -> None:
    mock_client = Mock()
    existing_dataset = {"id": "dataset-123", "name": "demo-dataset"}
    mock_client.datasets.list.return_value = [existing_dataset]
    writer = PhoenixWriter(client=mock_client)

    writer.create_examples_from_renderings([_phoenix_rendering()], dataset_name="demo-dataset")

    mock_client.datasets.create_dataset.assert_not_called()
    mock_client.datasets.add_examples_to_dataset.assert_called_once()
    assert mock_client.datasets.add_examples_to_dataset.call_args.kwargs["dataset"] == existing_dataset


def test_phoenix_writer_creates_dataset_when_missing() -> None:
    mock_client = Mock()
    mock_client.datasets.list.return_value = []
    writer = PhoenixWriter(client=mock_client)

    writer.create_examples_from_renderings([_phoenix_rendering()], dataset_name="demo-dataset")

    mock_client.datasets.create_dataset.assert_called_once()
    mock_client.datasets.add_examples_to_dataset.assert_not_called()
