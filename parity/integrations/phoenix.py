from __future__ import annotations

from typing import Any

from phoenix.client import Client

from parity.integrations._contracts import infer_method_kind_from_assertions, legacy_assertions, normalized_tags, parse_native_assertions
from parity.models import EvalCaseSnapshot, EvaluatorBindingCandidate, NativeEvalRendering


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


def _dataset_name(dataset: Any) -> str | None:
    if isinstance(dataset, dict):
        name = dataset.get("name")
        return str(name) if name is not None else None
    name = getattr(dataset, "name", None)
    return str(name) if name is not None else None


class PhoenixReader:
    def __init__(self, client: Client | None = None, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.client = client or Client(base_url=base_url, api_key=api_key)

    def fetch_examples(self, *, dataset_name: str, limit: int | None = None) -> list[EvalCaseSnapshot]:
        dataset = self.client.datasets.get_dataset(dataset=dataset_name)
        rows = _dataset_rows(dataset)
        if limit is not None:
            rows = rows[:limit]
        dataset_id = getattr(dataset, "id", dataset_name)
        name = getattr(dataset, "name", dataset_name)
        examples: list[EvalCaseSnapshot] = []
        for index, row in enumerate(rows):
            input_raw = None
            outputs: Any = {}
            metadata: dict[str, Any] = {}
            if isinstance(row, dict):
                input_raw = row.get("inputs") if isinstance(row.get("inputs"), dict) else row.get("input")
                outputs = row.get("outputs") or row.get("output") or {}
                if not outputs and row.get("expected_behavior") is not None:
                    outputs = {"expected_behavior": row.get("expected_behavior")}
                metadata = row.get("metadata", {}) or {}
            native_assertions = parse_native_assertions(
                metadata.get("parity_assertions"),
                assertion_id_prefix=str(row.get("id", f"{dataset_name}:{index}")) if isinstance(row, dict) else f"{dataset_name}:{index}",
                default_metadata=metadata,
            )
            if not native_assertions:
                native_assertions = legacy_assertions(
                    assertion_id_prefix=str(row.get("id", f"{dataset_name}:{index}")) if isinstance(row, dict) else f"{dataset_name}:{index}",
                    metadata=metadata,
                    expected_output=outputs,
                    assertion_type=metadata.get("assertion_type"),
                    rubric=metadata.get("rubric"),
                )
            method_kind = infer_method_kind_from_assertions(native_assertions)
            examples.append(
                EvalCaseSnapshot.model_validate(
                    {
                        "case_id": row.get("id", f"{dataset_name}:{index}") if isinstance(row, dict) else f"{dataset_name}:{index}",
                        "source_platform": "phoenix",
                        "source_target_id": str(dataset_id),
                        "source_target_name": str(name),
                        "target_locator": str(name),
                        "method_kind": method_kind,
                        "native_case": row if isinstance(row, dict) else {},
                        "native_input": input_raw,
                        "native_output": outputs,
                        "native_assertions": native_assertions,
                        "metadata": metadata,
                        "tags": normalized_tags(metadata.get("tags")),
                        "embedding": metadata.get("embedding"),
                        "embedding_model": metadata.get("embedding_model"),
                        "method_hints": ["phoenix_dataset"],
                        "method_confidence": 0.75 if native_assertions else 0.25,
                    }
                )
            )
        return examples

    def discover_evaluator_bindings(self, *, dataset_name: str) -> list[EvaluatorBindingCandidate]:
        # The current public Phoenix client used here exposes dataset/example and
        # experiment execution surfaces, but not dataset-evaluator CRUD.
        return []

    def read_evaluator_binding(self, binding_id: str, *, dataset_name: str) -> dict[str, Any]:
        raise KeyError(f"Unknown Phoenix evaluator binding: {binding_id}")

    def verify_evaluator_binding(self, binding_id: str, *, dataset_name: str) -> dict[str, Any]:
        return {
            "platform": "phoenix",
            "binding_id": binding_id,
            "verified": False,
            "verification_status": "unsupported",
            "note": "Phoenix dataset-evaluator verification is not exposed through the current client surface used by Parity.",
        }


class PhoenixWriter:
    def __init__(self, client: Client | None = None, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.client = client or Client(base_url=base_url, api_key=api_key)

    def _find_dataset(self, dataset_name: str) -> Any | None:
        for dataset in self.client.datasets.list(limit=None):
            if _dataset_name(dataset) == dataset_name:
                return dataset
        return None

    def create_examples_from_renderings(self, renderings: list[NativeEvalRendering], *, dataset_name: str) -> Any:
        inputs_list = []
        outputs_list = []
        metadata_list = []
        for rendering in renderings:
            if rendering.rendering_kind != "phoenix_example":
                continue
            payload = rendering.payload
            inputs_list.append(payload.get("inputs", {}))
            outputs_list.append(payload.get("outputs", {}))
            metadata = {
                **(payload.get("metadata") or {}),
                "rendering_id": rendering.rendering_id,
                "write_status": rendering.write_status,
            }
            if payload.get("tags") and not metadata.get("tags"):
                metadata["tags"] = list(payload.get("tags") or [])
            metadata_list.append(
                metadata
            )

        existing = self._find_dataset(dataset_name)
        if existing is None:
            return self.client.datasets.create_dataset(
                name=dataset_name,
                inputs=inputs_list,
                outputs=outputs_list,
                metadata=metadata_list,
                input_keys=sorted({key for item in inputs_list if isinstance(item, dict) for key in item}),
                output_keys=sorted({key for item in outputs_list if isinstance(item, dict) for key in item}),
                metadata_keys=sorted({key for item in metadata_list if isinstance(item, dict) for key in item}),
            )
        return self.client.datasets.add_examples_to_dataset(
            dataset=existing,
            inputs=inputs_list,
            outputs=outputs_list,
            metadata=metadata_list,
            input_keys=sorted({key for item in inputs_list if isinstance(item, dict) for key in item}),
            output_keys=sorted({key for item in outputs_list if isinstance(item, dict) for key in item}),
            metadata_keys=sorted({key for item in metadata_list if isinstance(item, dict) for key in item}),
        )
