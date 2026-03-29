from __future__ import annotations

import json
import uuid
from typing import Any

from langsmith import Client

from parity.models import EvalCase, ProbeCase, normalize_input
from parity.models.eval_case import ConversationMessage, flatten_expected_output

# Stable namespace for deterministic UUID generation from probe content.
# This keeps retries idempotent while avoiding collisions from reused probe IDs
# like "probe_001" across different proposals.
LANGSMITH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "parity-langsmith-probes")


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


def _serialize_langsmith_input(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump() if isinstance(item, ConversationMessage) else item for item in value]
    return value


def _langsmith_probe_inputs(probe: ProbeCase) -> dict[str, Any]:
    if probe.input_format == "conversation":
        return {"messages": _serialize_langsmith_input(probe.input)}
    if probe.input_format == "string":
        return {"query": probe.input}
    return _serialize_langsmith_input(probe.input)


def _langsmith_example_id(*, dataset_id: str, probe: ProbeCase) -> uuid.UUID:
    identity_payload = {
        "dataset_id": dataset_id,
        "inputs": _langsmith_probe_inputs(probe),
        "expected_behavior": probe.expected_behavior,
        "expected_behavior_type": probe.expected_behavior_type,
        "rubric": probe.rubric,
    }
    canonical_identity = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return uuid.uuid5(LANGSMITH_NAMESPACE, canonical_identity)


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
        dataset = self.client.read_dataset(dataset_name=dataset_name, dataset_id=dataset_id)
        resolved_dataset_id = str(dataset.id)

        examples = [
            {
                "id": _langsmith_example_id(dataset_id=resolved_dataset_id, probe=probe),
                "inputs": _langsmith_probe_inputs(probe),
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
            dataset_id=resolved_dataset_id,
            examples=examples,
        )
