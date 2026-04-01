from __future__ import annotations

from typing import Any

from parity.config import EvalEvaluatorConfig
from parity.integrations._contracts import serialize_native_assertions
from parity.models import (
    EvalCaseSnapshot,
    EvalMethodProfile,
    EvalTargetProfile,
    EvaluatorBindingCandidate,
    EvaluatorDossier,
    EvaluatorPlan,
    NativeEvalRendering,
    NativeAssertion,
    ProbeIntent,
    ResolvedEvalTarget,
)
from parity.models.eval_case import PRIORITY_EXPECTATION_KEYS, PRIORITY_INPUT_KEYS, flatten_expected_output


def summarize_raw_field_patterns(samples: list[EvalCaseSnapshot]) -> list[str]:
    patterns: list[str] = []
    for sample in samples:
        keys = sorted(sample.native_case.keys())
        if keys:
            pattern = ",".join(keys)
            if pattern not in patterns:
                patterns.append(pattern)
        for assertion in sample.native_assertions:
            descriptor = f"{assertion.assertion_kind}:{assertion.operator or 'operatorless'}"
            if descriptor not in patterns:
                patterns.append(descriptor)
    return patterns


def platform_evaluator_capabilities(platform: str) -> dict[str, Any]:
    normalized = "arize_phoenix" if platform == "phoenix" else platform
    if normalized == "promptfoo":
        return {
            "evaluator_scope": "row_local",
            "execution_surface": "config_file",
            "supports_formal_discovery": True,
            "supports_binding_verification": True,
            "supports_evaluator_reuse": True,
            "notes": [
                "Assertions are stored directly in the Promptfoo config row.",
                "No separate hosted evaluator object is required for automatic execution.",
            ],
        }
    if normalized == "langsmith":
        return {
            "evaluator_scope": "dataset_bound",
            "execution_surface": "sdk_experiment",
            "supports_formal_discovery": True,
            "supports_binding_verification": True,
            "supports_evaluator_reuse": True,
            "notes": [
                "Dataset examples and evaluator configuration are separate in LangSmith.",
                "The current host can formally inspect feedback configs and dataset/session feedback formulas through the LangSmith client.",
                "Current host support aligns rows to the existing active evaluator regime without mutating evaluator objects.",
            ],
        }
    if normalized == "braintrust":
        return {
            "evaluator_scope": "repo_code",
            "execution_surface": "repo_harness",
            "supports_formal_discovery": True,
            "supports_binding_verification": True,
            "supports_evaluator_reuse": True,
            "notes": [
                "Braintrust scorers are typically separate code or UI assets from dataset rows.",
                "Current host support preserves scorer hints and aligns new rows to the active scorer regime.",
            ],
        }
    if normalized == "arize_phoenix":
        return {
            "evaluator_scope": "dataset_bound",
            "execution_surface": "sdk_experiment",
            "supports_formal_discovery": False,
            "supports_binding_verification": False,
            "supports_evaluator_reuse": True,
            "notes": [
                "Phoenix dataset examples and evaluator attachments are separate surfaces.",
                "Current host support aligns rows to the existing evaluator regime without mutating dataset evaluators.",
            ],
        }
    return {
        "evaluator_scope": "unknown",
        "execution_surface": "unknown",
        "supports_formal_discovery": False,
        "supports_binding_verification": False,
        "supports_evaluator_reuse": False,
        "notes": ["No evaluator capability profile is defined for this platform."],
    }


def infer_method_profile(
    platform: str,
    samples: list[EvalCaseSnapshot],
    *,
    formal_candidates: list[EvaluatorBindingCandidate] | None = None,
    formal_notes: list[str] | None = None,
) -> EvalMethodProfile:
    capability = platform_evaluator_capabilities(platform)
    if not samples:
        return EvalMethodProfile(
            method_kind="unknown",
            input_shape="unknown",
            assertion_style="unknown",
            evaluator_scope=capability["evaluator_scope"],
            execution_surface=capability["execution_surface"],
            supports_evaluator_reuse=capability["supports_evaluator_reuse"],
            formal_discovery_status="confirmed" if formal_candidates else (
                "unsupported" if not capability["supports_formal_discovery"] else "fallback"
            ),
            formal_binding_count=len(formal_candidates or []),
            renderability_status="unsupported",
            confidence=0.0,
            notes=["No sample eval cases were available.", *(formal_notes or []), *capability["notes"]],
        )

    input_shapes: list[str] = []
    assertion_kinds: list[str] = []
    evaluator_binding: str | None = None
    notes: list[str] = []
    confidences: list[float] = []
    supports_multi_assert = False

    for sample in samples:
        projection = sample.normalized_projection
        input_shapes.append("conversation" if projection.is_conversational else _infer_input_shape(sample.native_input))
        supports_multi_assert = supports_multi_assert or len(sample.native_assertions) > 1
        confidences.append(sample.method_confidence)
        for assertion in sample.native_assertions:
            assertion_kinds.append(assertion.assertion_kind)
            if evaluator_binding is None:
                evaluator_binding = assertion.evaluator_name or assertion.metadata.get("evaluator_name")
        if sample.method_hints:
            notes.extend(sample.method_hints)

    method_kind = _dominant_method_kind(assertion_kinds)
    assertion_style = method_kind if method_kind != "unknown" else "unknown"
    if method_kind == "hybrid" and not supports_multi_assert:
        notes.append("Hybrid behavior inferred from mixed assertions, but the sampled cases did not prove multi-assert support.")

    renderability_status = _renderability_status_for(method_kind=method_kind, platform=platform, supports_multi_assert=supports_multi_assert)
    uses_judge = method_kind in {"judge", "hybrid"}
    confidence = min(1.0, max(confidences) if confidences else 0.5)
    inferred_candidates = _infer_binding_candidates(platform, samples, method_kind=method_kind, confidence=confidence)
    binding_candidates = _merge_binding_candidates(formal_candidates or [], inferred_candidates)
    if evaluator_binding is None and binding_candidates:
        evaluator_binding = binding_candidates[0].binding_id
    return EvalMethodProfile(
        method_kind=method_kind,
        input_shape=_dominant_value(input_shapes, default="unknown"),
        assertion_style=assertion_style,
        uses_judge=uses_judge,
        supports_multi_assert=supports_multi_assert,
        evaluator_binding=evaluator_binding,
        evaluator_scope=capability["evaluator_scope"],
        execution_surface=capability["execution_surface"],
        binding_candidates=binding_candidates,
        supports_evaluator_reuse=capability["supports_evaluator_reuse"],
        formal_discovery_status=_formal_discovery_status(
            capability=capability,
            formal_candidates=formal_candidates or [],
        ),
        formal_binding_count=len(formal_candidates or []),
        metadata_conventions=_collect_metadata_conventions(samples),
        renderability_status=renderability_status,
        confidence=confidence,
        notes=_dedupe([*notes, *(formal_notes or []), *capability["notes"]]),
    )


def _formal_discovery_status(
    *,
    capability: dict[str, Any],
    formal_candidates: list[EvaluatorBindingCandidate],
) -> str:
    if formal_candidates:
        return "confirmed"
    if capability["supports_formal_discovery"]:
        return "fallback"
    return "unsupported"


def _merge_binding_candidates(
    formal_candidates: list[EvaluatorBindingCandidate],
    inferred_candidates: list[EvaluatorBindingCandidate],
) -> list[EvaluatorBindingCandidate]:
    merged: dict[str, EvaluatorBindingCandidate] = {}
    for candidate in [*formal_candidates, *inferred_candidates]:
        existing = merged.get(candidate.binding_id)
        if existing is None:
            merged[candidate.binding_id] = candidate.model_copy(deep=True)
            continue
        if _candidate_priority(candidate) < _candidate_priority(existing):
            merged[candidate.binding_id] = candidate.model_copy(deep=True)
            existing = merged[candidate.binding_id]
        existing.confidence = max(existing.confidence, candidate.confidence)
        existing.reusable = existing.reusable or candidate.reusable
        existing.mapping_hints.update(candidate.mapping_hints)
        existing.notes = _dedupe([*existing.notes, *candidate.notes])
        if existing.binding_object_id is None:
            existing.binding_object_id = candidate.binding_object_id
        if existing.binding_location is None:
            existing.binding_location = candidate.binding_location
        if existing.binding_status == "unknown":
            existing.binding_status = candidate.binding_status
        if existing.verification_status != "verified":
            existing.verification_status = candidate.verification_status
    return sorted(
        merged.values(),
        key=lambda item: (_candidate_priority(item), -item.confidence, item.label.lower()),
    )


def _candidate_priority(candidate: EvaluatorBindingCandidate) -> int:
    if candidate.discovery_mode == "formal":
        return 0
    if candidate.discovery_mode == "repo_formal":
        return 1
    if candidate.discovery_mode == "inferred":
        return 2
    return 3


def _infer_binding_candidates(
    platform: str,
    samples: list[EvalCaseSnapshot],
    *,
    method_kind: str,
    confidence: float,
) -> list[EvaluatorBindingCandidate]:
    capability = platform_evaluator_capabilities(platform)
    candidates: dict[str, EvaluatorBindingCandidate] = {}

    def register(
        *,
        binding_id: str,
        label: str,
        source: str,
        discovery_mode: str = "inferred",
        confidence_value: float,
        mapping_hints: dict[str, str] | None = None,
        reusable: bool | None = None,
        binding_object_id: str | None = None,
        binding_location: str | None = None,
        binding_status: str = "unknown",
        verification_status: str = "unverified",
        notes: list[str] | None = None,
    ) -> None:
        existing = candidates.get(binding_id)
        candidate = EvaluatorBindingCandidate(
            binding_id=binding_id,
            label=label,
            scope=capability["evaluator_scope"],
            execution_surface=capability["execution_surface"],
            source=source,
            discovery_mode=discovery_mode,
            binding_object_id=binding_object_id,
            binding_location=binding_location,
            binding_status=binding_status,
            verification_status=verification_status,
            mapping_hints=mapping_hints or {},
            reusable=capability["supports_evaluator_reuse"] if reusable is None else reusable,
            confidence=max(0.0, min(confidence_value, 1.0)),
            notes=notes or [],
        )
        if existing is None:
            candidates[binding_id] = candidate
            return
        existing.confidence = max(existing.confidence, candidate.confidence)
        existing.reusable = existing.reusable or candidate.reusable
        existing.mapping_hints.update(candidate.mapping_hints)
        existing.notes = _dedupe([*existing.notes, *candidate.notes])

    for sample in samples:
        mapping_hints = _sample_mapping_hints(sample)
        sample_confidence = max(confidence, sample.method_confidence)
        metadata_binding = sample.metadata.get("preferred_evaluator_binding")
        if isinstance(metadata_binding, str) and metadata_binding.strip():
            register(
                binding_id=metadata_binding.strip(),
                label=metadata_binding.strip(),
                source="sample_metadata",
                discovery_mode="inferred",
                confidence_value=sample_confidence,
                mapping_hints=mapping_hints,
                notes=["Recovered from sample metadata."],
            )
        for assertion in sample.native_assertions:
            evaluator_name = assertion.evaluator_name or assertion.metadata.get("evaluator_name")
            if isinstance(evaluator_name, str) and evaluator_name.strip():
                register(
                    binding_id=evaluator_name.strip(),
                    label=evaluator_name.strip(),
                    source="native_assertion",
                    discovery_mode="inferred",
                    confidence_value=sample_confidence,
                    mapping_hints=mapping_hints,
                    notes=["Recovered from native assertion metadata."],
                )
            elif platform == "promptfoo" and assertion.assertion_kind == "judge":
                register(
                    binding_id="promptfoo::llm-rubric",
                    label="Promptfoo llm-rubric assertion",
                    source="native_assertion",
                    discovery_mode="inferred",
                    confidence_value=max(0.85, sample_confidence),
                    mapping_hints=mapping_hints,
                    notes=["Promptfoo judge behavior is encoded directly in the row-local assert block."],
                )

    if not candidates and method_kind in {"judge", "hybrid"}:
        default_label = {
            "langsmith": "Existing LangSmith evaluator regime",
            "braintrust": "Existing Braintrust scorer regime",
            "arize_phoenix": "Existing Phoenix evaluator regime",
            "phoenix": "Existing Phoenix evaluator regime",
            "promptfoo": "Promptfoo row-local assertion regime",
        }.get(platform, "Existing evaluator regime")
        register(
            binding_id=f"{platform}::default_evaluator_regime",
            label=default_label,
            source="heuristic",
            discovery_mode="heuristic",
            confidence_value=max(0.75, confidence * 0.95),
            reusable=capability["supports_evaluator_reuse"],
            notes=["Inferred from the existing target method and sampled assertion pattern."],
        )

    return sorted(candidates.values(), key=lambda item: item.confidence, reverse=True)


def build_evaluator_dossiers(
    platform: str,
    *,
    target_id: str,
    samples: list[EvalCaseSnapshot],
    method_profile: EvalMethodProfile,
) -> list[EvaluatorDossier]:
    dossiers: list[EvaluatorDossier] = []
    for index, candidate in enumerate(method_profile.binding_candidates, start=1):
        supporting_case_ids = _supporting_case_ids_for_candidate(samples, candidate)
        explicitness = _candidate_explicitness(candidate.source, candidate.discovery_mode)
        dossier_id = f"{target_id}::evaluator::{index:02d}"
        dossiers.append(
            EvaluatorDossier(
                dossier_id=dossier_id,
                target_id=target_id,
                binding_id=candidate.binding_id,
                label=candidate.label,
                scope=candidate.scope,
                execution_surface=candidate.execution_surface,
                source=candidate.source,
                discovery_mode=candidate.discovery_mode,
                binding_object_id=candidate.binding_object_id,
                binding_location=candidate.binding_location,
                binding_status=candidate.binding_status,
                verification_status=candidate.verification_status,
                explicitness=explicitness,
                mapping_hints=dict(candidate.mapping_hints),
                supporting_case_ids=supporting_case_ids,
                supporting_repo_asset_paths=[candidate.binding_location] if candidate.discovery_mode == "repo_formal" and candidate.binding_location else [],
                reuse_feasibility=_reuse_feasibility(candidate),
                confidence=candidate.confidence,
                rationale=_build_dossier_rationale(candidate, method_profile),
                risks=_build_dossier_risks(candidate, method_profile),
                notes=list(candidate.notes),
            )
        )
    if dossiers:
        return dossiers
    if method_profile.method_kind in {"judge", "hybrid"} and method_profile.evaluator_scope != "unknown":
        return [
            EvaluatorDossier(
                dossier_id=f"{target_id}::evaluator::01",
                target_id=target_id,
                binding_id=method_profile.evaluator_binding,
                label=method_profile.evaluator_binding or "Discovered evaluator regime",
                scope=method_profile.evaluator_scope,
                execution_surface=method_profile.execution_surface,
                source="method_profile",
                discovery_mode="heuristic",
                binding_object_id=None,
                binding_location=None,
                binding_status="unknown",
                verification_status="unverified",
                explicitness="heuristic",
                mapping_hints={},
                supporting_case_ids=[sample.case_id for sample in samples[:5]],
                supporting_repo_asset_paths=[],
                reuse_feasibility="likely" if method_profile.supports_evaluator_reuse else "unsupported",
                confidence=max(0.5, method_profile.confidence * 0.85),
                rationale="The target shows a judge-like evaluation regime, but no explicit evaluator binding candidate was recovered.",
                risks=["Binding identity is inferred from the target method profile rather than an explicit evaluator object."],
                notes=list(method_profile.notes),
            )
        ]
    return []


def build_native_rendering(
    intent: ProbeIntent,
    *,
    resolved_target: ResolvedEvalTarget,
    min_render_confidence: float,
) -> NativeEvalRendering:
    target = resolved_target.profile
    method_profile = resolved_target.method_profile
    render_confidence = min(
        1.0,
        (
            intent.testability_confidence
            + intent.target_fit_confidence
            + method_profile.confidence
            + target.profile_confidence
        )
        / 4.0,
    )
    if target.write_capability == "unsupported" or method_profile.renderability_status == "unsupported":
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="review_note",
            renderer_id=f"{target.platform}/unsupported",
            write_status="unsupported",
            render_confidence=render_confidence,
            target_locator=target.locator,
            summary="Native rendering is not supported for this target/method profile.",
            abstention_reason="No safe native renderer exists for the discovered eval method.",
        )

    review_only_reason = _review_only_reason_for_target(resolved_target)
    if (
        target.write_capability == "review_only"
        or render_confidence < min_render_confidence
        or method_profile.renderability_status == "review_only"
        or review_only_reason is not None
    ):
        payload = _review_only_payload(intent=intent, target=target, method_profile=method_profile)
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="review_note",
            renderer_id=f"{target.platform}/review_only",
            write_status="review_only",
            render_confidence=render_confidence,
            target_locator=target.locator,
            payload=payload,
            summary="Manual review is required before writing this eval.",
            abstention_reason=review_only_reason or "Render confidence or method support is insufficient for automatic writeback.",
        )

    if target.platform == "promptfoo":
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="promptfoo_test",
            renderer_id="promptfoo/native",
            write_status="native_ready",
            render_confidence=render_confidence,
            target_locator=target.locator,
            payload=_promptfoo_payload(intent, method_profile),
            file_path=target.locator,
            summary="Promptfoo-native test case ready for deterministic writeback.",
        )

    if target.platform == "langsmith":
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="langsmith_example",
            renderer_id="langsmith/example",
            write_status="native_ready",
            render_confidence=render_confidence,
            target_locator=target.locator,
            payload=_langsmith_payload(intent, resolved_target),
            summary="LangSmith-native example payload ready for deterministic writeback.",
        )

    if target.platform == "braintrust":
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="braintrust_record",
            renderer_id="braintrust/dataset-record",
            write_status="native_ready",
            render_confidence=render_confidence,
            target_locator=target.locator,
            payload=_braintrust_payload(intent, resolved_target),
            summary="Braintrust-native dataset record ready for deterministic writeback.",
        )

    if target.platform in {"phoenix", "arize_phoenix"}:
        return NativeEvalRendering(
            rendering_id=f"render-{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=target.target_id,
            method_kind=method_profile.method_kind,
            rendering_kind="phoenix_example",
            renderer_id="phoenix/dataset-example",
            write_status="native_ready",
            render_confidence=render_confidence,
            target_locator=target.locator,
            payload=_phoenix_payload(intent, resolved_target),
            summary="Phoenix-native dataset example payload ready for deterministic writeback.",
        )

    return NativeEvalRendering(
        rendering_id=f"render-{intent.intent_id}",
        intent_id=intent.intent_id,
        target_id=target.target_id,
        method_kind=method_profile.method_kind,
        rendering_kind="review_note",
        renderer_id=f"{target.platform}/unsupported",
        write_status="unsupported",
        render_confidence=render_confidence,
        target_locator=target.locator,
        summary="No native writer exists for the discovered platform.",
        abstention_reason=f"No writer contract is implemented for platform `{target.platform}`.",
    )


def build_evaluator_plan(
    intent: ProbeIntent,
    *,
    resolved_target: ResolvedEvalTarget,
    evaluator_config: EvalEvaluatorConfig,
) -> EvaluatorPlan:
    method_profile = resolved_target.method_profile
    base_confidence = min(
        1.0,
        (
            intent.target_fit_confidence
            + intent.testability_confidence
            + method_profile.confidence
            + resolved_target.profile.profile_confidence
        )
        / 4.0,
    )
    selected_dossier = _select_evaluator_dossier(intent, resolved_target)
    selected_candidate = _select_binding_candidate(intent, resolved_target, selected_dossier=selected_dossier)
    metadata: dict[str, Any] = {
        "method_kind": method_profile.method_kind,
        "renderability_status": method_profile.renderability_status,
        "formal_discovery_status": method_profile.formal_discovery_status,
    }
    if selected_dossier is not None:
        metadata["selected_dossier_discovery_mode"] = selected_dossier.discovery_mode
        metadata["selected_dossier_verification_status"] = selected_dossier.verification_status
    if selected_candidate is not None:
        metadata["candidate_source"] = selected_candidate.source
        metadata["candidate_reusable"] = selected_candidate.reusable
        metadata["candidate_binding_status"] = selected_candidate.binding_status
        metadata["candidate_discovery_mode"] = selected_candidate.discovery_mode

    if (
        method_profile.uses_judge
        and evaluator_config.formal_discovery_required
        and method_profile.formal_discovery_status != "confirmed"
        and not evaluator_config.allow_inference_fallback
    ):
        return EvaluatorPlan(
            plan_id=f"evaluator-plan::{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=intent.target_id,
            action="manual",
            scope=method_profile.evaluator_scope,
            execution_surface=method_profile.execution_surface,
            evaluator_dossier_id=selected_dossier.dossier_id if selected_dossier else intent.evaluator_dossier_id,
            confidence=max(0.4, base_confidence * 0.7),
            requires_opt_in=True,
            rationale="The target does not expose a formal evaluator discovery surface and inference fallback is disabled by policy.",
            metadata=metadata,
        )

    if (
        method_profile.uses_judge
        and evaluator_config.require_binding_verification
        and selected_dossier is not None
        and selected_dossier.verification_status != "verified"
    ):
        return EvaluatorPlan(
            plan_id=f"evaluator-plan::{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=intent.target_id,
            action="manual",
            scope=method_profile.evaluator_scope,
            execution_surface=method_profile.execution_surface,
            evaluator_dossier_id=selected_dossier.dossier_id,
            binding_ref=selected_dossier.binding_id,
            binding_label=selected_dossier.label,
            confidence=max(0.45, base_confidence * 0.75),
            requires_opt_in=True,
            rationale="The selected evaluator binding has not been formally verified and binding verification is required by policy.",
            metadata=metadata,
        )

    if method_profile.evaluator_scope == "row_local":
        return EvaluatorPlan(
            plan_id=f"evaluator-plan::{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=intent.target_id,
            action="row_local",
            scope=method_profile.evaluator_scope,
            execution_surface=method_profile.execution_surface,
            evaluator_dossier_id=selected_dossier.dossier_id if selected_dossier else intent.evaluator_dossier_id,
            binding_ref=selected_candidate.binding_id if selected_candidate else intent.preferred_evaluator_binding,
            binding_label=selected_candidate.label if selected_candidate else "Row-local assertions",
            confidence=max(base_confidence, selected_candidate.confidence if selected_candidate else 0.85),
            requires_opt_in=False,
            rationale="The discovered evaluator contract is row-local, so the native row/config write already carries the assertions.",
            metadata=metadata,
        )

    if method_profile.method_kind == "deterministic" and not method_profile.uses_judge:
        return EvaluatorPlan(
            plan_id=f"evaluator-plan::{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=intent.target_id,
            action="none",
            scope=method_profile.evaluator_scope,
            execution_surface=method_profile.execution_surface,
            evaluator_dossier_id=selected_dossier.dossier_id if selected_dossier else intent.evaluator_dossier_id,
            confidence=max(base_confidence, 0.8),
            requires_opt_in=False,
            rationale="The discovered target appears to evaluate deterministic reference outputs without a separate evaluator object.",
            metadata=metadata,
        )

    min_binding_confidence = evaluator_config.min_binding_confidence
    if (
        selected_candidate is not None
        and method_profile.supports_evaluator_reuse
        and selected_candidate.reusable
        and selected_candidate.confidence >= min_binding_confidence
        and selected_candidate.binding_status in {"attached", "row_local", "unknown"}
    ):
        return EvaluatorPlan(
            plan_id=f"evaluator-plan::{intent.intent_id}",
            intent_id=intent.intent_id,
            target_id=intent.target_id,
            action="reuse_existing",
            scope=selected_candidate.scope,
            execution_surface=selected_candidate.execution_surface,
            evaluator_dossier_id=selected_dossier.dossier_id if selected_dossier else intent.evaluator_dossier_id,
            binding_ref=selected_candidate.binding_id,
            binding_label=selected_candidate.label,
            confidence=max(base_confidence, selected_candidate.confidence),
            requires_opt_in=False,
            rationale="A compatible evaluator regime is already present on the resolved target, so Parity should align to it rather than creating a new one.",
            metadata=metadata,
        )

    return EvaluatorPlan(
        plan_id=f"evaluator-plan::{intent.intent_id}",
        intent_id=intent.intent_id,
        target_id=intent.target_id,
        action="manual",
        scope=method_profile.evaluator_scope,
        execution_surface=method_profile.execution_surface,
        evaluator_dossier_id=selected_dossier.dossier_id if selected_dossier else intent.evaluator_dossier_id,
        binding_ref=selected_candidate.binding_id if selected_candidate else intent.preferred_evaluator_binding,
        binding_label=selected_candidate.label if selected_candidate else intent.preferred_evaluator_binding,
        confidence=max(0.4, base_confidence * 0.75),
        requires_opt_in=False,
        rationale="Parity could not confirm a safe existing evaluator regime for this target, so the proposal should be reviewed manually before adoption.",
        metadata=metadata,
    )


def _langsmith_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> dict[str, Any]:
    method_profile = resolved_target.method_profile
    tags = _intent_tags(intent, method_profile)
    output_binding = _resolve_output_binding(intent, resolved_target)
    reference_output = _reference_output_payload(intent, resolved_target, wrap_scalars=True)
    metadata = _dataset_metadata(
        intent,
        resolved_target,
        output_binding=output_binding,
        tags=tags,
        assertions=_build_native_assertions(intent, resolved_target),
    )
    return {
        "inputs": _langsmith_inputs_payload(intent, resolved_target),
        "outputs": reference_output,
        "metadata": metadata,
        "tags": tags,
    }


def _braintrust_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> dict[str, Any]:
    method_profile = resolved_target.method_profile
    tags = _intent_tags(intent, method_profile)
    output_binding = _resolve_output_binding(intent, resolved_target)
    metadata = _dataset_metadata(
        intent,
        resolved_target,
        output_binding=output_binding,
        tags=tags,
        assertions=_build_native_assertions(intent, resolved_target),
    )
    return {
        "input": _braintrust_input_payload(intent, resolved_target),
        "expected": _reference_output_payload(intent, resolved_target, wrap_scalars=False),
        "metadata": metadata,
        "tags": tags,
    }


def _phoenix_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> dict[str, Any]:
    method_profile = resolved_target.method_profile
    tags = _intent_tags(intent, method_profile)
    output_binding = _resolve_output_binding(intent, resolved_target)
    metadata = _dataset_metadata(
        intent,
        resolved_target,
        output_binding=output_binding,
        tags=tags,
        assertions=_build_native_assertions(intent, resolved_target),
    )
    return {
        "inputs": _langsmith_inputs_payload(intent, resolved_target),
        "outputs": _reference_output_payload(intent, resolved_target, wrap_scalars=True),
        "metadata": metadata,
        "tags": tags,
    }


def _build_native_assertions(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> list[NativeAssertion]:
    method_profile = resolved_target.method_profile
    output_binding = _resolve_output_binding(intent, resolved_target)
    reference_output = _reference_output_payload(
        intent,
        resolved_target,
        wrap_scalars=resolved_target.profile.platform != "braintrust",
    )
    deterministic_operator = next(
        (hint for hint in intent.native_assertion_hints if hint in {"equals", "contains", "not-contains", "javascript"}),
        None,
    )
    assertions: list[NativeAssertion] = []
    if method_profile.method_kind in {"deterministic", "hybrid"}:
        assertions.append(
            NativeAssertion.model_validate(
                {
                    "assertion_id": f"{intent.intent_id}:deterministic",
                    "assertion_kind": "deterministic",
                    "operator": deterministic_operator or "contains",
                    "expected_value": flatten_expected_output(reference_output) or intent.pass_criteria,
                    "metadata": {"output_binding": output_binding} if output_binding else {},
                }
            )
        )
    if method_profile.method_kind in {"judge", "hybrid"}:
        assertions.append(
            NativeAssertion.model_validate(
                {
                    "assertion_id": f"{intent.intent_id}:judge",
                    "assertion_kind": "judge",
                    "operator": "llm-rubric",
                    "rubric": intent.pass_criteria,
                    "evaluator_name": intent.preferred_evaluator_binding,
                    "metadata": {"output_binding": output_binding} if output_binding else {},
                }
            )
        )
    return assertions


def _dataset_metadata(
    intent: ProbeIntent,
    resolved_target: ResolvedEvalTarget,
    *,
    output_binding: str | None,
    tags: list[str],
    assertions: list[NativeAssertion],
) -> dict[str, Any]:
    method_profile = resolved_target.method_profile
    input_binding = _resolve_input_binding(intent, resolved_target)
    metadata: dict[str, Any] = {
        "generated_by": "parity",
        "intent_type": intent.intent_type,
        "probe_rationale": intent.probe_rationale,
        "failure_mode": intent.failure_mode,
        "related_risk_flag": intent.related_risk_flag,
        "method_kind": method_profile.method_kind,
        "renderability": method_profile.renderability_status,
    }
    metadata.update(intent.native_metadata_hints)
    if intent.evaluator_dossier_id:
        metadata["evaluator_dossier_id"] = intent.evaluator_dossier_id
    if intent.preferred_evaluator_binding:
        metadata["preferred_evaluator_binding"] = intent.preferred_evaluator_binding
    if intent.native_shape_notes:
        metadata["native_shape_notes"] = list(intent.native_shape_notes)
    if method_profile.method_kind in {"judge", "hybrid"}:
        metadata["rubric"] = intent.pass_criteria
    if method_profile.method_kind in {"deterministic", "hybrid"}:
        metadata["deterministic_expectation"] = flatten_expected_output(
            _reference_output_payload(
                intent,
                resolved_target,
                wrap_scalars=resolved_target.profile.platform != "braintrust",
            )
        ) or intent.pass_criteria
    if tags:
        hinted_tags = metadata.get("tags")
        existing_tags = hinted_tags if isinstance(hinted_tags, list) else []
        metadata["tags"] = _dedupe([*tags, *existing_tags])
    metadata["parity_contract_version"] = 1
    metadata["parity_assertions"] = serialize_native_assertions(assertions)
    if input_binding:
        metadata["parity_input_binding"] = input_binding
    if output_binding:
        metadata["parity_output_binding"] = output_binding
    return metadata


def _intent_tags(intent: ProbeIntent, method_profile: EvalMethodProfile) -> list[str]:
    return _dedupe([*intent.native_tag_hints, intent.intent_type, method_profile.method_kind, "parity"])


def _langsmith_inputs_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> dict[str, Any]:
    binding = _resolve_input_binding(intent, resolved_target)
    if intent.input_format == "conversation":
        return {binding or "messages": _serialize_input(intent.input)}
    if intent.input_format == "dict":
        serialized = _serialize_input(intent.input)
        if isinstance(serialized, dict):
            if binding and binding not in serialized and _samples_prefer_wrapped_input(resolved_target, binding):
                return {binding: serialized}
            return serialized
        return {binding or "input": serialized}
    return {binding or "query": intent.input}


def _braintrust_input_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> Any:
    binding = _resolve_input_binding(intent, resolved_target)
    if intent.input_format == "dict":
        serialized = _serialize_input(intent.input)
        if isinstance(serialized, dict) and not (binding and binding not in serialized and _braintrust_prefers_mapping_input(resolved_target)):
            return serialized
        if binding and _braintrust_prefers_mapping_input(resolved_target):
            return {binding: serialized}
        return serialized
    if intent.input_format == "conversation":
        serialized = _serialize_input(intent.input)
        if binding and _braintrust_prefers_mapping_input(resolved_target):
            return {binding: serialized}
        return serialized
    if binding and _braintrust_prefers_mapping_input(resolved_target):
        return {binding: intent.input}
    return intent.input


def _reference_output_payload(intent: ProbeIntent, resolved_target: ResolvedEvalTarget, *, wrap_scalars: bool) -> Any:
    method_profile = resolved_target.method_profile
    raw_reference = intent.native_reference_output
    if raw_reference is None and method_profile.method_kind in {"deterministic", "hybrid"}:
        raw_reference = intent.pass_criteria
    if raw_reference is None:
        return {} if wrap_scalars else None
    serialized = _serialize_input(raw_reference)
    if isinstance(serialized, dict):
        return serialized
    binding = _resolve_output_binding(intent, resolved_target)
    if wrap_scalars:
        return {binding or "expected_behavior": serialized}
    if isinstance(serialized, list):
        if binding and _braintrust_prefers_wrapped_output(resolved_target):
            return {binding: serialized}
        return serialized
    if binding and _braintrust_prefers_wrapped_output(resolved_target):
        return {binding: serialized}
    return serialized


def _resolve_input_binding(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> str | None:
    if intent.native_input_binding:
        return intent.native_input_binding
    bindings = [
        _preferred_key_from_mapping(container, PRIORITY_INPUT_KEYS)
        for container in _sample_input_containers(resolved_target)
    ]
    bindings = [binding for binding in bindings if binding]
    if bindings:
        return _dominant_value(bindings, default="query")
    if intent.input_format == "conversation":
        return "messages"
    return None if resolved_target.profile.platform == "braintrust" else "query"


def _resolve_output_binding(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> str | None:
    if intent.native_output_binding:
        return intent.native_output_binding
    bindings = [
        _preferred_key_from_mapping(container, PRIORITY_EXPECTATION_KEYS)
        for container in _sample_output_containers(resolved_target)
    ]
    bindings = [binding for binding in bindings if binding]
    if bindings:
        return _dominant_value(bindings, default="expected_behavior")
    return None if resolved_target.profile.platform == "braintrust" else "expected_behavior"


def _select_evaluator_dossier(intent: ProbeIntent, resolved_target: ResolvedEvalTarget) -> EvaluatorDossier | None:
    preferred_dossier = (intent.evaluator_dossier_id or "").strip()
    if preferred_dossier:
        for dossier in resolved_target.evaluator_dossiers:
            if dossier.dossier_id == preferred_dossier:
                return dossier
    preferred_binding = (intent.preferred_evaluator_binding or "").strip().lower()
    if preferred_binding:
        for dossier in resolved_target.evaluator_dossiers:
            if preferred_binding in {
                (dossier.binding_id or "").strip().lower(),
                dossier.label.strip().lower(),
            }:
                return dossier
    return resolved_target.evaluator_dossiers[0] if resolved_target.evaluator_dossiers else None


def _candidate_from_dossier(
    dossier: EvaluatorDossier,
    resolved_target: ResolvedEvalTarget,
) -> EvaluatorBindingCandidate | None:
    binding_id = (dossier.binding_id or "").strip().lower()
    if binding_id:
        for candidate in resolved_target.method_profile.binding_candidates:
            if candidate.binding_id.strip().lower() == binding_id:
                return candidate
    if dossier.binding_id:
        return EvaluatorBindingCandidate(
            binding_id=dossier.binding_id,
            label=dossier.label,
            scope=dossier.scope,
            execution_surface=dossier.execution_surface,
            source=dossier.source,
            discovery_mode=dossier.discovery_mode,
            binding_object_id=dossier.binding_object_id,
            binding_location=dossier.binding_location,
            binding_status=dossier.binding_status,
            verification_status=dossier.verification_status,
            mapping_hints=dict(dossier.mapping_hints),
            reusable=dossier.reuse_feasibility in {"confirmed", "likely"},
            confidence=dossier.confidence,
            notes=list(dossier.notes),
        )
    return None


def _select_binding_candidate(
    intent: ProbeIntent,
    resolved_target: ResolvedEvalTarget,
    *,
    selected_dossier: EvaluatorDossier | None = None,
) -> EvaluatorBindingCandidate | None:
    if selected_dossier is not None:
        dossier_candidate = _candidate_from_dossier(selected_dossier, resolved_target)
        if dossier_candidate is not None:
            return dossier_candidate
    candidates = list(resolved_target.method_profile.binding_candidates)
    if not candidates:
        return None
    preferred = (intent.preferred_evaluator_binding or "").strip().lower()
    if preferred:
        for candidate in candidates:
            if preferred in {candidate.binding_id.lower(), candidate.label.lower()}:
                return candidate
    return candidates[0]


def _supporting_case_ids_for_candidate(
    samples: list[EvalCaseSnapshot],
    candidate: EvaluatorBindingCandidate,
) -> list[str]:
    supporting: list[str] = []
    binding_id = (candidate.binding_id or "").strip()
    for sample in samples:
        matched = False
        preferred_binding = sample.metadata.get("preferred_evaluator_binding")
        if isinstance(preferred_binding, str) and preferred_binding.strip() == binding_id:
            matched = True
        if not matched:
            for assertion in sample.native_assertions:
                evaluator_name = assertion.evaluator_name or assertion.metadata.get("evaluator_name")
                if isinstance(evaluator_name, str) and evaluator_name.strip() == binding_id:
                    matched = True
                    break
                if binding_id == "promptfoo::llm-rubric" and assertion.assertion_kind == "judge":
                    matched = True
                    break
        if not matched and candidate.source in {"heuristic", "method_profile"} and sample.method_kind in {"judge", "hybrid"}:
            matched = True
        if matched and sample.case_id not in supporting:
            supporting.append(sample.case_id)
    return supporting


def _candidate_explicitness(source: str, discovery_mode: str | None = None) -> str:
    if discovery_mode in {"formal", "repo_formal"}:
        return "explicit"
    if source in {"sample_metadata", "native_assertion"}:
        return "explicit"
    if source in {"repo_asset", "method_profile"}:
        return "inferred"
    return "heuristic"


def _reuse_feasibility(candidate: EvaluatorBindingCandidate) -> str:
    if not candidate.reusable:
        return "unsupported"
    if candidate.confidence >= 0.85:
        return "confirmed"
    if candidate.confidence >= 0.65:
        return "likely"
    return "uncertain"


def _build_dossier_rationale(candidate: EvaluatorBindingCandidate, method_profile: EvalMethodProfile) -> str:
    if candidate.discovery_mode == "formal":
        return "Recovered from a platform-managed evaluator object or binding attached to the resolved target."
    if candidate.discovery_mode == "repo_formal":
        return "Recovered from a repo-managed eval harness or scorer definition associated with the resolved target."
    if candidate.source == "native_assertion":
        return "Recovered from sampled native assertions on the resolved target."
    if candidate.source == "sample_metadata":
        return "Recovered from sampled example metadata on the resolved target."
    if candidate.source == "repo_asset":
        return "Recovered from repo-local eval asset evidence."
    if candidate.source == "method_profile":
        return "Recovered from the resolved target method profile."
    return (
        "Inferred from the target's sampled assertion pattern and evaluator scope."
        if method_profile.method_kind in {"judge", "hybrid"}
        else "No strong evaluator binding evidence was recovered."
    )


def _build_dossier_risks(candidate: EvaluatorBindingCandidate, method_profile: EvalMethodProfile) -> list[str]:
    risks: list[str] = []
    if candidate.source == "heuristic":
        risks.append("Binding identity is heuristic rather than directly observed.")
    if candidate.discovery_mode == "inferred":
        risks.append("Binding identity is inferred from target evidence rather than a formal evaluator object.")
    if candidate.binding_status == "available":
        risks.append("This evaluator is available evidence, but it is not confirmed as the active target regime.")
    if method_profile.evaluator_scope in {"dataset_bound", "experiment_bound", "project_bound", "repo_code"}:
        risks.append("This platform manages evaluators outside the row payload.")
    return risks


def _sample_mapping_hints(sample: EvalCaseSnapshot) -> dict[str, str]:
    hints: dict[str, str] = {}
    input_binding = _preferred_key_from_mapping(sample.native_case.get("inputs", {}), PRIORITY_INPUT_KEYS) if isinstance(sample.native_case.get("inputs"), dict) else None
    if input_binding is None and isinstance(sample.native_case.get("vars"), dict):
        input_binding = _preferred_key_from_mapping(sample.native_case["vars"], PRIORITY_INPUT_KEYS)
    if input_binding is None and isinstance(sample.native_case.get("input"), dict):
        input_binding = _preferred_key_from_mapping(sample.native_case["input"], PRIORITY_INPUT_KEYS)
    output_binding = None
    if isinstance(sample.native_case.get("outputs"), dict):
        output_binding = _preferred_key_from_mapping(sample.native_case["outputs"], PRIORITY_EXPECTATION_KEYS)
    elif isinstance(sample.native_case.get("expected"), dict):
        output_binding = _preferred_key_from_mapping(sample.native_case["expected"], PRIORITY_EXPECTATION_KEYS)
    elif isinstance(sample.native_output, dict):
        output_binding = _preferred_key_from_mapping(sample.native_output, PRIORITY_EXPECTATION_KEYS)
    if isinstance(sample.metadata.get("parity_input_binding"), str):
        hints["input"] = sample.metadata["parity_input_binding"]
    elif input_binding:
        hints["input"] = input_binding
    if isinstance(sample.metadata.get("parity_output_binding"), str):
        hints["reference_output"] = sample.metadata["parity_output_binding"]
    elif output_binding:
        hints["reference_output"] = output_binding
    return hints


def _sample_input_containers(resolved_target: ResolvedEvalTarget) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for sample in resolved_target.samples:
        containers.extend(_sample_input_containers_from_case(sample))
    return containers


def _sample_output_containers(resolved_target: ResolvedEvalTarget) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for sample in resolved_target.samples:
        containers.extend(_sample_output_containers_from_case(sample))
    return containers


def _sample_input_containers_from_case(sample: EvalCaseSnapshot) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    native_case = sample.native_case
    for key in ("inputs", "vars"):
        candidate = native_case.get(key)
        if isinstance(candidate, dict):
            containers.append(candidate)
    candidate = native_case.get("input")
    if isinstance(candidate, dict):
        containers.append(candidate)
    return containers


def _sample_output_containers_from_case(sample: EvalCaseSnapshot) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    native_case = sample.native_case
    candidate = native_case.get("outputs")
    if isinstance(candidate, dict):
        containers.append(candidate)
    candidate = native_case.get("expected")
    if isinstance(candidate, dict):
        containers.append(candidate)
    if isinstance(sample.native_output, dict):
        containers.append(sample.native_output)
    return containers


def _samples_prefer_wrapped_input(resolved_target: ResolvedEvalTarget, binding: str) -> bool:
    containers = _sample_input_containers(resolved_target)
    if not containers:
        return False
    threshold = max(1, (len(containers) + 1) // 2)
    return sum(1 for container in containers if set(container) == {binding}) >= threshold


def _braintrust_prefers_mapping_input(resolved_target: ResolvedEvalTarget) -> bool:
    inputs = [sample.native_case.get("input") for sample in resolved_target.samples if "input" in sample.native_case]
    if not inputs:
        return False
    threshold = max(1, (len(inputs) + 1) // 2)
    return sum(isinstance(item, dict) for item in inputs) >= threshold


def _braintrust_prefers_wrapped_output(resolved_target: ResolvedEvalTarget) -> bool:
    expected_values = [sample.native_case.get("expected") for sample in resolved_target.samples if "expected" in sample.native_case]
    if not expected_values:
        return False
    threshold = max(1, (len(expected_values) + 1) // 2)
    return sum(isinstance(item, dict) for item in expected_values) >= threshold


def _preferred_key_from_mapping(mapping: dict[str, Any], priorities: tuple[str, ...]) -> str | None:
    for key in priorities:
        if key in mapping:
            return key
    return sorted(mapping)[0] if mapping else None


def _promptfoo_payload(intent: ProbeIntent, method_profile: EvalMethodProfile) -> dict[str, Any]:
    input_binding = intent.native_input_binding or ("messages" if intent.input_format == "conversation" else None)
    if intent.input_format == "conversation":
        vars_payload: dict[str, Any] = {input_binding or "messages": _serialize_input(intent.input)}
    elif intent.input_format == "dict":
        serialized_input = _serialize_input(intent.input)
        vars_payload = dict(serialized_input)
        if input_binding and input_binding not in vars_payload:
            vars_payload = {input_binding: serialized_input}
    else:
        vars_payload = {input_binding or "query": intent.input}

    assertions = []
    deterministic_operator = next(
        (hint for hint in intent.native_assertion_hints if hint in {"equals", "contains", "not-contains", "javascript"}),
        None,
    )
    if method_profile.method_kind in {"deterministic", "hybrid"}:
        assertions.append({"type": deterministic_operator or "contains", "value": intent.pass_criteria})
    if method_profile.method_kind in {"judge", "hybrid"}:
        assertions.append({"type": "llm-rubric", "value": intent.pass_criteria})
    if not assertions:
        assertions.append({"type": "contains", "value": intent.pass_criteria})

    metadata = {
        "probe_rationale": intent.probe_rationale,
        "failure_mode": intent.failure_mode,
        "related_risk_flag": intent.related_risk_flag,
        "method_kind": method_profile.method_kind,
    }
    if intent.evaluator_dossier_id:
        metadata["evaluator_dossier_id"] = intent.evaluator_dossier_id
    if intent.preferred_evaluator_binding:
        metadata["preferred_evaluator_binding"] = intent.preferred_evaluator_binding
    if intent.native_shape_notes:
        metadata["native_shape_notes"] = list(intent.native_shape_notes)
    if intent.native_tag_hints:
        metadata["tags"] = list(intent.native_tag_hints)
    metadata.update(intent.native_metadata_hints)

    return {
        "id": intent.intent_id,
        "description": f"[{intent.intent_type}] {intent.title}",
        "vars": vars_payload,
        "assert": assertions,
        "metadata": metadata,
    }


def _review_only_payload(
    *,
    intent: ProbeIntent,
    target: EvalTargetProfile,
    method_profile: EvalMethodProfile,
) -> dict[str, Any]:
    return {
        "target": target.target_name,
        "method_kind": method_profile.method_kind,
        "title": intent.title,
        "behavior_under_test": intent.behavior_under_test,
        "pass_criteria": intent.pass_criteria,
        "failure_mode": intent.failure_mode,
        "input": _serialize_input(intent.input),
    }


def _review_only_reason_for_target(resolved_target: ResolvedEvalTarget) -> str | None:
    profile = resolved_target.profile
    if profile.platform == "braintrust" and not (profile.project or "").strip():
        return "Braintrust writeback requires a project-scoped dataset target, but the resolved target is missing `project`."
    return None


def _dominant_method_kind(assertion_kinds: list[str]) -> str:
    kinds = set(assertion_kinds)
    if not kinds:
        return "unknown"
    if "trajectory" in kinds:
        return "trajectory"
    if "pairwise" in kinds:
        return "pairwise"
    if "human_review" in kinds:
        return "human_review"
    if "judge" in kinds and "deterministic" in kinds:
        return "hybrid"
    if "hybrid" in kinds:
        return "hybrid"
    if "judge" in kinds:
        return "judge"
    if "deterministic" in kinds:
        return "deterministic"
    return next(iter(kinds))


def _renderability_status_for(*, method_kind: str, platform: str, supports_multi_assert: bool) -> str:
    if method_kind in {"deterministic", "judge"}:
        return "native_ready"
    if method_kind == "hybrid":
        return "native_ready" if platform == "promptfoo" or supports_multi_assert else "review_only"
    if method_kind in {"pairwise", "human_review", "trajectory"}:
        return "review_only"
    return "unsupported"


def _dominant_value(values: list[str], *, default: str) -> str:
    if not values:
        return default
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=counts.get)


def _collect_metadata_conventions(samples: list[EvalCaseSnapshot]) -> dict[str, Any]:
    metadata_keys: dict[str, int] = {}
    input_keys: dict[str, int] = {}
    output_keys: dict[str, int] = {}
    input_shapes: list[str] = []
    output_shapes: list[str] = []
    for sample in samples:
        for key in sample.metadata:
            metadata_keys[key] = metadata_keys.get(key, 0) + 1
        for container in _sample_input_containers_from_case(sample):
            for key in container:
                input_keys[key] = input_keys.get(key, 0) + 1
        for container in _sample_output_containers_from_case(sample):
            for key in container:
                output_keys[key] = output_keys.get(key, 0) + 1
        input_shapes.append(_infer_input_shape(sample.native_input))
        output_shapes.append("dict" if isinstance(sample.native_output, dict) else "scalar")
    return {
        "common_keys": sorted(key for key, count in metadata_keys.items() if count >= 2),
        "common_input_keys": sorted(key for key, count in input_keys.items() if count >= 2),
        "common_output_keys": sorted(key for key, count in output_keys.items() if count >= 2),
        "input_shapes": sorted({shape for shape in input_shapes if shape}),
        "output_shapes": sorted({shape for shape in output_shapes if shape}),
    }


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _serialize_input(value: Any) -> Any:
    if isinstance(value, list):
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in value]
    return value


def _inputs_payload(intent: ProbeIntent) -> dict[str, Any]:
    if intent.input_format == "conversation":
        return {"messages": _serialize_input(intent.input)}
    if intent.input_format == "dict":
        serialized = _serialize_input(intent.input)
        return serialized if isinstance(serialized, dict) else {"input": serialized}
    return {"query": intent.input}


def _infer_input_shape(value: Any) -> str:
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "conversation"
    return "unknown"
