from __future__ import annotations

from typing import Any

import braintrust

from parity.errors import PlatformIntegrationError
from parity.models import EvalCase, ProbeCase, normalize_input
from parity.models.eval_case import ConversationMessage, flatten_expected_output


class BraintrustReader:
    """Read access is expected to be MCP-mediated in Stage 2."""

    def fetch_examples(self, *args: Any, **kwargs: Any) -> list[EvalCase]:
        raise PlatformIntegrationError(
            "BraintrustReader is MCP-mediated in Stage 2; use BraintrustDirectReader as a fallback."
        )


class BraintrustDirectReader:
    def __init__(self, *, api_key: str | None = None, org_name: str | None = None) -> None:
        self.api_key = api_key
        self.org_name = org_name

    def fetch_examples(
        self,
        *,
        project: str,
        dataset_name: str,
        limit: int | None = None,
    ) -> list[EvalCase]:
        dataset = braintrust.init_dataset(
            project=project,
            name=dataset_name,
            api_key=self.api_key,
            org_name=self.org_name,
        )
        rows = dataset.fetch()
        examples: list[EvalCase] = []
        for index, row in enumerate(rows):
            if limit is not None and index >= limit:
                break
            input_raw = row.get("input")
            metadata = row.get("metadata") or {}
            examples.append(
                EvalCase.model_validate(
                    {
                        "id": row.get("id") or f"{dataset_name}:{index}",
                        "source_platform": "braintrust",
                        "source_dataset_id": str(getattr(dataset, "id", dataset_name)),
                        "source_dataset_name": dataset_name,
                        "input_raw": input_raw,
                        "input_normalized": normalize_input(input_raw),
                        "expected_output": flatten_expected_output(row.get("expected")),
                        "rubric": metadata.get("rubric"),
                        "assertion_type": metadata.get("assertion_type"),
                        "metadata": metadata,
                        "tags": list(row.get("tags") or []),
                        "embedding": metadata.get("embedding"),
                        "embedding_model": metadata.get("embedding_model"),
                    }
                )
            )
        return examples


class BraintrustWriter:
    def __init__(self, *, api_key: str | None = None, org_name: str | None = None) -> None:
        self.api_key = api_key
        self.org_name = org_name

    def create_examples(
        self,
        probes: list[ProbeCase],
        *,
        project: str,
        dataset_name: str,
    ) -> Any:
        dataset = braintrust.init_dataset(
            project=project,
            name=dataset_name,
            api_key=self.api_key,
            org_name=self.org_name,
        )
        def serialize_input(value: Any) -> Any:
            if isinstance(value, list):
                return [item.model_dump() if isinstance(item, ConversationMessage) else item for item in value]
            return value
        inserted_ids = []
        for probe in probes:
            inserted_ids.append(
                dataset.insert(
                    input=serialize_input(probe.input),
                    expected=probe.expected_behavior,
                    metadata={
                        "probe_type": probe.probe_type,
                        "rationale": probe.probe_rationale,
                        "rubric": probe.rubric,
                        "generated_by": "parity",
                        "probe_id": probe.probe_id,
                        "assertion_type": probe.expected_behavior_type,
                    },
                    tags=[probe.probe_type, "parity"],
                    id=probe.probe_id,
                )
            )
        if hasattr(dataset, "flush"):
            dataset.flush()
        return inserted_ids
