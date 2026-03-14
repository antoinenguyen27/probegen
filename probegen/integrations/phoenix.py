from __future__ import annotations

from typing import Any

from phoenix.client import Client

from probegen.models import EvalCase, ProbeCase, normalize_input
from probegen.models.eval_case import ConversationMessage, flatten_expected_output


def _dataset_rows(dataset: Any) -> list[dict[str, Any]]:
    if dataset is None:
        return []
    if isinstance(dataset, dict):
        rows = dataset.get("examples")
        return rows if isinstance(rows, list) else []
    if hasattr(dataset, "examples"):
        rows = getattr(dataset, "examples")
        if isinstance(rows, list):
            return rows
    if hasattr(dataset, "model_dump"):
        dumped = dataset.model_dump()
        rows = dumped.get("examples")
        return rows if isinstance(rows, list) else []
    return []


class PhoenixReader:
    def __init__(self, client: Client | None = None, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.client = client or Client(base_url=base_url, api_key=api_key)

    def fetch_examples(self, *, dataset_name: str, limit: int | None = None) -> list[EvalCase]:
        dataset = self.client.datasets.get_dataset(dataset=dataset_name)
        rows = _dataset_rows(dataset)
        if limit is not None:
            rows = rows[:limit]
        dataset_id = getattr(dataset, "id", dataset_name)
        name = getattr(dataset, "name", dataset_name)
        examples: list[EvalCase] = []
        for index, row in enumerate(rows):
            input_raw = row.get("input") if isinstance(row, dict) else None
            metadata = row.get("metadata", {}) if isinstance(row, dict) else {}
            examples.append(
                EvalCase.model_validate(
                    {
                        "id": row.get("id", f"{dataset_name}:{index}") if isinstance(row, dict) else f"{dataset_name}:{index}",
                        "source_platform": "phoenix",
                        "source_dataset_id": str(dataset_id),
                        "source_dataset_name": str(name),
                        "input_raw": input_raw,
                        "input_normalized": normalize_input(input_raw),
                        "expected_output": flatten_expected_output(row.get("expected_behavior") if isinstance(row, dict) else None),
                        "rubric": metadata.get("rubric"),
                        "assertion_type": metadata.get("assertion_type"),
                        "metadata": metadata,
                        "tags": list(metadata.get("tags") or []),
                        "embedding": metadata.get("embedding"),
                        "embedding_model": metadata.get("embedding_model"),
                    }
                )
            )
        return examples


class PhoenixWriter:
    def __init__(self, client: Client | None = None, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.client = client or Client(base_url=base_url, api_key=api_key)

    def _find_dataset(self, dataset_name: str) -> Any | None:
        for dataset in self.client.datasets.list(limit=None):
            if getattr(dataset, "name", None) == dataset_name:
                return dataset
        return None

    def create_examples(self, probes: list[ProbeCase], *, dataset_name: str) -> Any:
        def serialize_input(value: Any) -> Any:
            if isinstance(value, list):
                return [item.model_dump() if isinstance(item, ConversationMessage) else item for item in value]
            return value
        rows = [
            {
                "input": serialize_input(probe.input),
                "expected_behavior": probe.expected_behavior,
                "probe_type": probe.probe_type,
                "rationale": probe.probe_rationale,
                "probe_id": probe.probe_id,
                "rubric": probe.rubric,
                "assertion_type": probe.expected_behavior_type,
            }
            for probe in probes
        ]
        existing = self._find_dataset(dataset_name)
        if existing is None:
            return self.client.datasets.create_dataset(
                name=dataset_name,
                examples=rows,
                input_keys=["input"],
                output_keys=["expected_behavior"],
                metadata_keys=["probe_type", "rationale", "probe_id", "rubric", "assertion_type"],
            )
        return self.client.datasets.add_examples_to_dataset(
            dataset=existing,
            examples=rows,
            input_keys=["input"],
            output_keys=["expected_behavior"],
            metadata_keys=["probe_type", "rationale", "probe_id", "rubric", "assertion_type"],
        )
