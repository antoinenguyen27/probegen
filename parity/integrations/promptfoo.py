from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from parity.models import EvalCaseSnapshot, EvaluatorBindingCandidate, NativeAssertion, NativeEvalRendering, normalize_input


_PROMPTFOO_JUDGE_ASSERTION_TYPES = {
    "llm-rubric",
    "g-eval",
    "answer-relevance",
    "context-faithfulness",
    "context-recall",
    "factuality",
}


def _promptfoo_assertion_kind(assertion_type: str | None) -> str:
    normalized = (assertion_type or "").strip().lower()
    if not normalized:
        return "unknown"
    if normalized in _PROMPTFOO_JUDGE_ASSERTION_TYPES or normalized.startswith("model-graded"):
        return "judge"
    return "deterministic"


def _serialize_rendering_input(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
    return value


class PromptfooReader:
    def fetch_examples(self, config_path: str | Path) -> list[EvalCaseSnapshot]:
        path = Path(config_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tests = payload.get("tests", [])
        dataset_name = path.stem
        examples: list[EvalCaseSnapshot] = []
        for index, test in enumerate(tests):
            vars_payload = test.get("vars", {})
            input_raw = vars_payload.get("messages") or vars_payload.get("query") or vars_payload
            assertions = test.get("assert", [])
            native_assertions = []
            for assertion_index, assertion in enumerate(assertions):
                assertion_type = assertion.get("type")
                native_assertions.append(
                    NativeAssertion.model_validate(
                        {
                            "assertion_id": f"{test.get('id', f'{dataset_name}:{index}')}:{assertion_index}",
                            "assertion_kind": _promptfoo_assertion_kind(assertion_type),
                            "operator": assertion_type,
                            "expected_value": assertion.get("value") if assertion_type != "llm-rubric" else None,
                            "rubric": assertion.get("value") if assertion_type == "llm-rubric" else None,
                            "metadata": {"raw_assertion": assertion},
                        }
                    )
                )
            method_kind = "unknown"
            kinds = {assertion.assertion_kind for assertion in native_assertions}
            if "judge" in kinds and "deterministic" in kinds:
                method_kind = "hybrid"
            elif "judge" in kinds:
                method_kind = "judge"
            elif "deterministic" in kinds:
                method_kind = "deterministic"
            examples.append(
                EvalCaseSnapshot.model_validate(
                    {
                        "case_id": test.get("id", f"{dataset_name}:{index}"),
                        "source_platform": "promptfoo",
                        "source_target_id": str(path),
                        "source_target_name": dataset_name,
                        "target_locator": str(path),
                        "method_kind": method_kind,
                        "native_case": test,
                        "native_input": input_raw,
                        "native_output": {"assert": assertions},
                        "native_assertions": native_assertions,
                        "metadata": {
                            "description": test.get("description"),
                            "metadata": test.get("metadata", {}),
                        },
                        "tags": list((test.get("metadata") or {}).get("tags", [])),
                        "method_hints": ["promptfoo_config"],
                        "method_confidence": 0.8 if assertions else 0.2,
                    }
                )
            )
        return examples

    def discover_evaluator_bindings(self, config_path: str | Path) -> list[EvaluatorBindingCandidate]:
        path = Path(config_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tests = payload.get("tests", [])
        candidates: dict[str, EvaluatorBindingCandidate] = {}

        for index, test in enumerate(tests):
            assertions = test.get("assert", [])
            for assertion_index, assertion in enumerate(assertions):
                assertion_type = assertion.get("type")
                normalized_assertion_type = (assertion_type or "").strip().lower()
                if _promptfoo_assertion_kind(assertion_type) != "judge":
                    continue
                binding_id = f"promptfoo::{normalized_assertion_type}"
                location = f"{path}:{index}:{assertion_index}"
                existing = candidates.get(binding_id)
                candidate = EvaluatorBindingCandidate.model_validate(
                    {
                        "binding_id": binding_id,
                        "label": f"Promptfoo {normalized_assertion_type} assertion",
                        "scope": "row_local",
                        "execution_surface": "config_file",
                        "source": "promptfoo_config",
                        "discovery_mode": "formal",
                        "binding_object_id": location,
                        "binding_location": location,
                        "binding_status": "row_local",
                        "verification_status": "verified",
                        "mapping_hints": {},
                        "reusable": True,
                        "confidence": 0.99,
                        "notes": ["Recovered directly from the Promptfoo assert block."],
                    }
                )
                if existing is None:
                    candidates[binding_id] = candidate
                else:
                    existing.notes = list(dict.fromkeys([*existing.notes, *candidate.notes]))
        return list(candidates.values())

    def read_evaluator_binding(self, config_path: str | Path, binding_id: str) -> dict[str, Any]:
        candidates = self.discover_evaluator_bindings(config_path)
        for candidate in candidates:
            if candidate.binding_id == binding_id:
                return candidate.model_dump(mode="json")
        raise KeyError(f"Unknown Promptfoo evaluator binding: {binding_id}")

    def verify_evaluator_binding(self, config_path: str | Path, binding_id: str) -> dict[str, Any]:
        path = Path(config_path)
        exists = any(candidate.binding_id == binding_id for candidate in self.discover_evaluator_bindings(path))
        return {
            "platform": "promptfoo",
            "binding_id": binding_id,
            "verified": exists,
            "verification_status": "verified" if exists else "unverified",
            "binding_location": str(path),
        }


class PromptfooWriter:
    def write_renderings(
        self,
        renderings: list[NativeEvalRendering],
        *,
        test_file: str | Path,
        artifact_path: str | None = None,
        pr_number: int | None = None,
        version: str = "0.1.0",
        commit_sha: str = "",
    ) -> dict[str, Path]:
        path = Path(test_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            payload = {}

        tests = payload.get("tests", [])
        for rendering in renderings:
            if rendering.rendering_kind != "promptfoo_test":
                continue
            tests.append(rendering.payload)
        payload["description"] = (
            f"Parity evals for {artifact_path or 'artifacts'}"
            + (f" (PR #{pr_number})" if pr_number is not None else "")
        )
        payload["tests"] = tests
        header = f"# Generated by parity v{version} — commit {commit_sha}\n"
        path.write_text(header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")

        outputs = {"test_file": path}
        if any(
            isinstance(rendering.payload.get("vars", {}).get("messages"), list)
            for rendering in renderings
            if rendering.rendering_kind == "promptfoo_test"
        ):
            prompt_dir = path.parent / "prompts"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = prompt_dir / "conversational_probe_prompt.json"
            prompt_path.write_text("[]\n", encoding="utf-8")
            outputs["prompt_file"] = prompt_path
        return outputs


def rendering_to_promptfoo_test(rendering: NativeEvalRendering) -> dict[str, Any]:
    if rendering.rendering_kind != "promptfoo_test":
        raise ValueError("Expected a promptfoo_test rendering")
    payload = dict(rendering.payload)
    vars_payload = payload.get("vars")
    if isinstance(vars_payload, dict):
        payload["vars"] = {
            key: _serialize_rendering_input(value) for key, value in vars_payload.items()
        }
    return payload
