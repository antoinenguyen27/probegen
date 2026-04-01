from __future__ import annotations

import json
import uuid
from typing import Any

from langsmith import Client

from parity.integrations._contracts import infer_method_kind_from_assertions, legacy_assertions, normalized_tags, parse_native_assertions
from parity.models import EvalCaseSnapshot, EvaluatorBindingCandidate, NativeEvalRendering

LANGSMITH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "parity-langsmith-evals")


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


def _langsmith_example_id(*, dataset_id: str, rendering: NativeEvalRendering) -> uuid.UUID:
    identity_payload = {
        "dataset_id": dataset_id,
        "payload": rendering.payload,
        "renderer_id": rendering.renderer_id,
    }
    canonical_identity = json.dumps(identity_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return uuid.uuid5(LANGSMITH_NAMESPACE, canonical_identity)


def _dataset_selector(*, dataset_name: str | None = None, dataset_id: str | None = None) -> dict[str, str]:
    if dataset_id:
        return {"dataset_id": dataset_id}
    if dataset_name:
        return {"dataset_name": dataset_name}
    raise ValueError("LangSmith operations require either dataset_id or dataset_name.")


class LangSmithReader:
    def __init__(self, client: Client | None = None, *, api_key: str | None = None) -> None:
        self.client = client or Client(api_key=api_key)

    def fetch_examples(
        self,
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
        limit: int | None = None,
    ) -> list[EvalCaseSnapshot]:
        dataset = self.client.read_dataset(**_dataset_selector(dataset_name=dataset_name, dataset_id=dataset_id))
        examples = self.client.list_examples(dataset_id=str(dataset.id), limit=limit)
        normalized: list[EvalCaseSnapshot] = []
        for example in examples:
            inputs = _as_mapping(getattr(example, "inputs", None))
            outputs = _as_mapping(getattr(example, "outputs", None))
            metadata = _as_mapping(getattr(example, "metadata", None))
            input_raw = inputs.get("query") or inputs.get("input") or inputs.get("messages") or inputs
            native_assertions = parse_native_assertions(
                metadata.get("parity_assertions"),
                assertion_id_prefix=str(getattr(example, "id")),
                default_metadata=metadata,
            )
            if not native_assertions:
                native_assertions = legacy_assertions(
                    assertion_id_prefix=str(getattr(example, "id")),
                    metadata=metadata,
                    expected_output=outputs,
                    assertion_type=metadata.get("assertion_type"),
                    rubric=metadata.get("rubric"),
                )
            method_kind = infer_method_kind_from_assertions(native_assertions)
            normalized.append(
                EvalCaseSnapshot.model_validate(
                    {
                        "case_id": str(getattr(example, "id")),
                        "source_platform": "langsmith",
                        "source_target_id": str(dataset.id),
                        "source_target_name": dataset.name,
                        "target_locator": dataset.name,
                        "method_kind": method_kind,
                        "native_case": {
                            "inputs": inputs,
                            "outputs": outputs,
                            "metadata": metadata,
                        },
                        "native_input": input_raw,
                        "native_output": outputs,
                        "native_assertions": native_assertions,
                        "metadata": metadata,
                        "tags": normalized_tags(metadata.get("tags")),
                        "embedding": metadata.get("embedding"),
                        "embedding_model": metadata.get("embedding_model"),
                        "method_hints": ["langsmith_dataset"],
                        "method_confidence": 0.75 if native_assertions else 0.25,
                    }
                )
            )
        return normalized

    def discover_evaluator_bindings(
        self,
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
    ) -> list[EvaluatorBindingCandidate]:
        dataset = self.client.read_dataset(**_dataset_selector(dataset_name=dataset_name, dataset_id=dataset_id))
        resolved_dataset_id = str(dataset.id)
        candidates: dict[str, EvaluatorBindingCandidate] = {}

        def register(candidate: EvaluatorBindingCandidate) -> None:
            existing = candidates.get(candidate.binding_id)
            if existing is None:
                candidates[candidate.binding_id] = candidate
                return
            existing.confidence = max(existing.confidence, candidate.confidence)
            existing.notes = list(dict.fromkeys([*existing.notes, *candidate.notes]))
            existing.mapping_hints.update(candidate.mapping_hints)

        formula_keys: set[str] = set()
        try:
            for formula in self.client.list_feedback_formulas(dataset_id=resolved_dataset_id):
                formula_keys.add(formula.feedback_key)
                register(
                    EvaluatorBindingCandidate.model_validate(
                        {
                            "binding_id": f"langsmith::dataset_formula::{formula.feedback_key}",
                            "label": f"LangSmith dataset formula `{formula.feedback_key}`",
                            "scope": "dataset_bound",
                            "execution_surface": "sdk_experiment",
                            "source": "feedback_formula",
                            "discovery_mode": "formal",
                            "binding_object_id": str(formula.id),
                            "binding_location": f"dataset:{resolved_dataset_id}/feedback-formulas/{formula.id}",
                            "binding_status": "attached",
                            "verification_status": "verified",
                            "mapping_hints": {},
                            "reusable": True,
                            "confidence": 0.97,
                            "notes": [
                                f"Dataset-scoped feedback formula using {formula.aggregation_type}.",
                                f"Weighted keys: {', '.join(part.key for part in formula.formula_parts)}",
                            ],
                        }
                    )
                )
        except Exception:
            pass

        for project in self._list_reference_projects(reference_dataset_id=resolved_dataset_id):
            project_id = str(getattr(project, "id", ""))
            project_name = str(getattr(project, "name", "") or project_id)
            if not project_id:
                continue
            try:
                formulas = self.client.list_feedback_formulas(session_id=project_id)
            except Exception:
                continue
            for formula in formulas:
                formula_keys.add(formula.feedback_key)
                register(
                    EvaluatorBindingCandidate.model_validate(
                        {
                            "binding_id": f"langsmith::session_formula::{project_id}::{formula.feedback_key}",
                            "label": f"LangSmith session formula `{formula.feedback_key}` ({project_name})",
                            "scope": "experiment_bound",
                            "execution_surface": "sdk_experiment",
                            "source": "feedback_formula",
                            "discovery_mode": "formal",
                            "binding_object_id": str(formula.id),
                            "binding_location": f"session:{project_id}/feedback-formulas/{formula.id}",
                            "binding_status": "attached",
                            "verification_status": "verified",
                            "mapping_hints": {},
                            "reusable": True,
                            "confidence": 0.95,
                            "notes": [
                                f"Session/project-scoped feedback formula attached to `{project_name}`.",
                                f"Weighted keys: {', '.join(part.key for part in formula.formula_parts)}",
                            ],
                        }
                    )
                )

        if formula_keys:
            try:
                configs = self.client.list_feedback_configs(feedback_key=sorted(formula_keys))
            except Exception:
                configs = []
            for config in configs:
                register(
                    EvaluatorBindingCandidate.model_validate(
                        {
                            "binding_id": f"langsmith::feedback_config::{config.feedback_key}",
                            "label": f"LangSmith feedback config `{config.feedback_key}`",
                            "scope": "project_bound",
                            "execution_surface": "sdk_experiment",
                            "source": "feedback_config",
                            "discovery_mode": "formal",
                            "binding_object_id": config.feedback_key,
                            "binding_location": f"feedback-configs/{config.feedback_key}",
                            "binding_status": "available",
                            "verification_status": "verified",
                            "mapping_hints": {},
                            "reusable": True,
                            "confidence": 0.9,
                            "notes": [
                                f"Feedback config type: {config.feedback_config.get('type')}",
                                "Configuration is defined at the workspace level and can be reused by evaluators or formulas.",
                            ],
                        }
                    )
                )

        return sorted(candidates.values(), key=lambda item: (item.discovery_mode != "formal", -item.confidence, item.label.lower()))

    def read_evaluator_binding(
        self,
        binding_id: str,
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        for candidate in self.discover_evaluator_bindings(dataset_name=dataset_name, dataset_id=dataset_id):
            if candidate.binding_id == binding_id:
                return candidate.model_dump(mode="json")
        raise KeyError(f"Unknown LangSmith evaluator binding: {binding_id}")

    def verify_evaluator_binding(
        self,
        binding_id: str,
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            binding = self.read_evaluator_binding(binding_id, dataset_name=dataset_name, dataset_id=dataset_id)
            return {
                "platform": "langsmith",
                "binding_id": binding_id,
                "verified": True,
                "verification_status": "verified",
                "binding_location": binding.get("binding_location"),
            }
        except KeyError:
            return {
                "platform": "langsmith",
                "binding_id": binding_id,
                "verified": False,
                "verification_status": "unverified",
            }

    def _list_reference_projects(self, *, reference_dataset_id: str) -> list[Any]:
        try:
            return list(self.client.list_projects(reference_dataset_id=reference_dataset_id))
        except Exception:
            return []


class LangSmithWriter:
    def __init__(self, client: Client | None = None, *, api_key: str | None = None) -> None:
        self.client = client or Client(api_key=api_key)

    def create_examples_from_renderings(
        self,
        renderings: list[NativeEvalRendering],
        *,
        dataset_name: str | None = None,
        dataset_id: str | None = None,
        source_pr: int | None = None,
        source_commit: str | None = None,
    ) -> Any:
        if dataset_id:
            resolved_dataset_id = str(dataset_id)
        else:
            dataset = self.client.read_dataset(**_dataset_selector(dataset_name=dataset_name, dataset_id=dataset_id))
            resolved_dataset_id = str(dataset.id)
        examples = []
        for rendering in renderings:
            if rendering.rendering_kind != "langsmith_example":
                continue
            payload = rendering.payload
            metadata = dict(payload.get("metadata") or {})
            if payload.get("tags") and not metadata.get("tags"):
                metadata["tags"] = list(payload.get("tags") or [])
            metadata.update(
                {
                    "generated_by": "parity",
                    "source_pr": source_pr,
                    "source_commit": source_commit,
                    "rendering_id": rendering.rendering_id,
                    "write_status": rendering.write_status,
                }
            )
            examples.append(
                {
                    "id": _langsmith_example_id(dataset_id=resolved_dataset_id, rendering=rendering),
                    "inputs": payload.get("inputs", {}),
                    "outputs": payload.get("outputs", {}),
                    "metadata": metadata,
                }
            )
        return self.client.create_examples(dataset_id=resolved_dataset_id, examples=examples)
