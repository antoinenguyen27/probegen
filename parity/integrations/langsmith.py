from __future__ import annotations

from typing import Any

from langsmith import Client

from parity.models import EvalCase, ProbeCase, normalize_input
from parity.models.eval_case import ConversationMessage, flatten_expected_output


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


class LangSmithReader:
    def __init__(self, client: Client | None = None, *, api_key: str | None = None) -> None:
        self.client = client or Client(api_key=api_key)

    def fetch_examples(
        self,
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
    ) -> list[EvalCase]:
        dataset = self.client.read_dataset(dataset_name=dataset_name, dataset_id=dataset_id)
        examples = self.client.list_examples(dataset_id=str(dataset.id), limit=limit)
        normalized: list[EvalCase] = []
        for example in examples:
            inputs = _as_mapping(getattr(example, "inputs", None))
            outputs = _as_mapping(getattr(example, "outputs", None))
            metadata = _as_mapping(getattr(example, "metadata", None))
            input_raw = inputs.get("query") or inputs.get("input") or inputs.get("messages") or inputs
            normalized.append(
                EvalCase.model_validate(
                    {
                        "id": str(getattr(example, "id")),
                        "source_platform": "langsmith",
                        "source_dataset_id": str(dataset.id),
                        "source_dataset_name": dataset.name,
                        "input_raw": input_raw,
                        "input_normalized": normalize_input(input_raw),
                        "expected_output": flatten_expected_output(outputs),
                        "rubric": metadata.get("rubric"),
                        "assertion_type": metadata.get("assertion_type"),
                        "metadata": metadata,
                        "tags": list(metadata.get("tags", [])),
                        "embedding": metadata.get("embedding"),
                        "embedding_model": metadata.get("embedding_model"),
                    }
                )
            )
        return normalized


class LangSmithWriter:
    def __init__(self, client: Client | None = None, *, api_key: str | None = None) -> None:
        self.client = client or Client(api_key=api_key)

    def create_examples(
        self,
        probes: list[ProbeCase],
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
        source_pr: int | None = None,
        source_commit: str | None = None,
    ) -> Any:
        def serialize_input(value: Any) -> Any:
            if isinstance(value, list):
                return [item.model_dump() if isinstance(item, ConversationMessage) else item for item in value]
            return value

        examples = [
            {
                "id": probe.probe_id,
                "inputs": (
                    {"messages": serialize_input(probe.input)}
                    if probe.input_format == "conversation"
                    else {"query": probe.input} if probe.input_format == "string" else serialize_input(probe.input)
                ),
                "outputs": {"expected_behavior": probe.expected_behavior},
                "metadata": {
                    "probe_type": probe.probe_type,
                    "rationale": probe.probe_rationale,
                    "rubric": probe.rubric,
                    "generated_by": "parity",
                    "source_pr": source_pr,
                    "source_commit": source_commit,
                    "probe_id": probe.probe_id,
                    "assertion_type": probe.expected_behavior_type,
                },
            }
            for probe in probes
        ]
        return self.client.create_examples(
            dataset_name=dataset_name,
            dataset_id=dataset_id,
            examples=examples,
        )
