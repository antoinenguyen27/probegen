from __future__ import annotations

from parity.config import EvalEvaluatorConfig
from parity.models import EvalCaseSnapshot, EvalMethodProfile, EvalTargetProfile, EvaluatorBindingCandidate, ProbeIntent, ResolvedEvalTarget
from parity.renderers import build_evaluator_dossiers, build_evaluator_plan, build_native_rendering, infer_method_profile


def test_build_native_rendering_uses_discovered_langsmith_bindings() -> None:
    target = ResolvedEvalTarget.model_validate(
        {
            "profile": {
                "target_id": "langsmith::demo",
                "platform": "langsmith",
                "locator": "demo-dataset",
                "target_name": "demo-dataset",
                "resolution_source": "platform_discovery",
                "access_mode": "sdk",
                "write_capability": "native_ready",
                "profile_confidence": 0.9,
            },
            "method_profile": {
                "method_kind": "hybrid",
                "input_shape": "string",
                "assertion_style": "hybrid",
                "uses_judge": True,
                "supports_multi_assert": True,
                "renderability_status": "native_ready",
                "confidence": 0.9,
            },
            "samples": [
                EvalCaseSnapshot.model_validate(
                    {
                        "case_id": "case-1",
                        "source_platform": "langsmith",
                        "source_target_id": "langsmith::demo",
                        "source_target_name": "demo-dataset",
                        "target_locator": "demo-dataset",
                        "method_kind": "hybrid",
                        "native_case": {
                            "inputs": {"question": "What year was the Paris Agreement signed?"},
                            "outputs": {"answer": "2015"},
                            "metadata": {"tags": ["citation"]},
                        },
                        "native_input": "What year was the Paris Agreement signed?",
                        "native_output": {"answer": "2015"},
                        "native_assertions": [
                            {
                                "assertion_id": "case-1:0",
                                "assertion_kind": "deterministic",
                                "operator": "equals",
                                "expected_value": "2015",
                            },
                            {
                                "assertion_id": "case-1:1",
                                "assertion_kind": "judge",
                                "operator": "llm-rubric",
                                "rubric": "The answer cites a source.",
                            },
                        ],
                        "method_confidence": 0.9,
                    }
                )
            ],
        }
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": "intent-1",
            "gap_id": "gap-1",
            "target_id": "langsmith::demo",
            "method_kind": "hybrid",
            "intent_type": "expected_improvement",
            "title": "Cited factual answer",
            "is_conversational": False,
            "input": "What year was the Paris Agreement signed?",
            "input_format": "string",
            "behavior_under_test": "The assistant gives the year and cites a source.",
            "pass_criteria": "The answer states 2015 and cites a source.",
            "failure_mode": "The answer omits the citation.",
            "probe_rationale": "Protect the updated citation requirement.",
            "related_risk_flag": "Factual answers may omit citations.",
            "specificity_confidence": 0.9,
            "testability_confidence": 0.9,
            "novelty_confidence": 0.8,
            "realism_confidence": 0.8,
            "target_fit_confidence": 0.9,
        }
    )

    rendering = build_native_rendering(intent, resolved_target=target, min_render_confidence=0.5)

    assert rendering.rendering_kind == "langsmith_example"
    assert rendering.payload["inputs"] == {"question": "What year was the Paris Agreement signed?"}
    assert rendering.payload["outputs"] == {"answer": "The answer states 2015 and cites a source."}
    assert rendering.payload["metadata"]["parity_input_binding"] == "question"
    assert rendering.payload["metadata"]["parity_output_binding"] == "answer"
    assert len(rendering.payload["metadata"]["parity_assertions"]) == 2


def test_build_native_rendering_preserves_scalar_braintrust_expected_shape() -> None:
    target = ResolvedEvalTarget.model_validate(
        {
            "profile": {
                "target_id": "braintrust::demo",
                "platform": "braintrust",
                "locator": "demo-dataset",
                "target_name": "demo-dataset",
                "project": "demo-project",
                "resolution_source": "platform_discovery",
                "access_mode": "sdk",
                "write_capability": "native_ready",
                "profile_confidence": 0.9,
            },
            "method_profile": {
                "method_kind": "deterministic",
                "input_shape": "string",
                "assertion_style": "deterministic",
                "uses_judge": False,
                "supports_multi_assert": False,
                "renderability_status": "native_ready",
                "confidence": 0.9,
            },
            "samples": [
                {
                    "case_id": "case-1",
                    "source_platform": "braintrust",
                    "source_target_id": "braintrust::demo",
                    "source_target_name": "demo-dataset",
                    "target_locator": "demo-dataset",
                    "project": "demo-project",
                    "method_kind": "deterministic",
                    "native_case": {"input": "What year was the Paris Agreement signed?", "expected": "2015"},
                    "native_input": "What year was the Paris Agreement signed?",
                    "native_output": "2015",
                    "native_assertions": [
                        {
                            "assertion_id": "case-1:0",
                            "assertion_kind": "deterministic",
                            "operator": "equals",
                            "expected_value": "2015",
                        }
                    ],
                    "method_confidence": 0.9,
                }
            ],
        }
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": "intent-2",
            "gap_id": "gap-2",
            "target_id": "braintrust::demo",
            "method_kind": "deterministic",
            "intent_type": "regression_guard",
            "title": "Scalar expected output",
            "is_conversational": False,
            "input": "What year was the Paris Agreement signed?",
            "input_format": "string",
            "behavior_under_test": "The assistant returns the correct year.",
            "pass_criteria": "2015",
            "failure_mode": "The assistant returns the wrong year.",
            "probe_rationale": "Preserve scalar expected values for Braintrust rows.",
            "related_risk_flag": "Wrong factual answer.",
            "specificity_confidence": 0.9,
            "testability_confidence": 0.9,
            "novelty_confidence": 0.8,
            "realism_confidence": 0.8,
            "target_fit_confidence": 0.9,
        }
    )

    rendering = build_native_rendering(intent, resolved_target=target, min_render_confidence=0.5)

    assert rendering.rendering_kind == "braintrust_record"
    assert rendering.payload["input"] == "What year was the Paris Agreement signed?"
    assert rendering.payload["expected"] == "2015"


def test_build_native_rendering_marks_braintrust_target_without_project_as_review_only() -> None:
    target = ResolvedEvalTarget.model_validate(
        {
            "profile": {
                "target_id": "braintrust::demo",
                "platform": "braintrust",
                "locator": "demo-dataset",
                "target_name": "demo-dataset",
                "project": None,
                "resolution_source": "platform_discovery",
                "access_mode": "sdk",
                "write_capability": "native_ready",
                "profile_confidence": 0.9,
            },
            "method_profile": {
                "method_kind": "deterministic",
                "input_shape": "string",
                "assertion_style": "deterministic",
                "uses_judge": False,
                "supports_multi_assert": False,
                "renderability_status": "native_ready",
                "confidence": 0.9,
            },
            "samples": [],
        }
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": "intent-2",
            "gap_id": "gap-2",
            "target_id": "braintrust::demo",
            "method_kind": "deterministic",
            "intent_type": "regression_guard",
            "title": "Scalar expected output",
            "is_conversational": False,
            "input": "What year was the Paris Agreement signed?",
            "input_format": "string",
            "behavior_under_test": "The assistant returns the correct year.",
            "pass_criteria": "2015",
            "failure_mode": "The assistant returns the wrong year.",
            "probe_rationale": "Preserve scalar expected values for Braintrust rows.",
            "related_risk_flag": "Wrong factual answer.",
            "specificity_confidence": 0.9,
            "testability_confidence": 0.9,
            "novelty_confidence": 0.8,
            "realism_confidence": 0.8,
            "target_fit_confidence": 0.9,
        }
    )

    rendering = build_native_rendering(intent, resolved_target=target, min_render_confidence=0.5)

    assert rendering.rendering_kind == "review_note"
    assert rendering.write_status == "review_only"
    assert "missing `project`" in (rendering.abstention_reason or "")


def test_infer_method_profile_exposes_row_local_evaluator_candidates_for_promptfoo() -> None:
    samples = [
        EvalCaseSnapshot.model_validate(
            {
                "case_id": "case-1",
                "source_platform": "promptfoo",
                "source_target_id": "promptfoo::demo",
                "source_target_name": "demo",
                "target_locator": "promptfooconfig.yaml",
                "method_kind": "hybrid",
                "native_case": {
                    "vars": {"messages": [{"role": "user", "content": "hi"}]},
                    "assert": [{"type": "llm-rubric", "value": "Friendly reply."}],
                },
                "native_input": [{"role": "user", "content": "hi"}],
                "native_output": {"assert": [{"type": "llm-rubric", "value": "Friendly reply."}]},
                "native_assertions": [
                    {
                        "assertion_id": "case-1:0",
                        "assertion_kind": "judge",
                        "operator": "llm-rubric",
                        "rubric": "Friendly reply.",
                    }
                ],
                "method_confidence": 0.85,
            }
        )
    ]

    profile = infer_method_profile("promptfoo", samples)

    assert profile.evaluator_scope == "row_local"
    assert profile.execution_surface == "config_file"
    assert profile.binding_candidates[0].binding_id == "promptfoo::llm-rubric"


def test_infer_method_profile_prefers_formal_candidates_over_inferred_candidates() -> None:
    sample = EvalCaseSnapshot.model_validate(
        {
            "case_id": "case-1",
            "source_platform": "langsmith",
            "source_target_id": "langsmith::demo",
            "source_target_name": "demo",
            "target_locator": "demo-dataset",
            "method_kind": "judge",
            "native_case": {
                "inputs": {"query": "hi"},
                "outputs": {"answer": "hello"},
                "metadata": {"preferred_evaluator_binding": "inferred-helpfulness"},
            },
            "native_input": "hi",
            "native_output": {"answer": "hello"},
            "native_assertions": [
                {
                    "assertion_id": "case-1:0",
                    "assertion_kind": "judge",
                    "operator": "llm-rubric",
                    "rubric": "Helpful answer.",
                }
            ],
            "method_confidence": 0.8,
        }
    )
    formal = [
        EvaluatorBindingCandidate.model_validate(
            {
                "binding_id": "langsmith::dataset_formula::helpfulness",
                "label": "LangSmith dataset formula `helpfulness`",
                "scope": "dataset_bound",
                "execution_surface": "sdk_experiment",
                "source": "feedback_formula",
                "discovery_mode": "formal",
                "binding_object_id": "formula-123",
                "binding_location": "dataset:123/feedback-formulas/formula-123",
                "binding_status": "attached",
                "verification_status": "verified",
                "reusable": True,
                "confidence": 0.97,
            }
        )
    ]

    profile = infer_method_profile("langsmith", [sample], formal_candidates=formal)

    assert profile.formal_discovery_status == "confirmed"
    assert profile.formal_binding_count == 1
    assert profile.binding_candidates[0].binding_id == "langsmith::dataset_formula::helpfulness"


def test_build_evaluator_plan_prefers_reuse_for_existing_judge_regime() -> None:
    target = ResolvedEvalTarget.model_validate(
        {
            "profile": {
                "target_id": "langsmith::demo",
                "platform": "langsmith",
                "locator": "demo-dataset",
                "target_name": "demo-dataset",
                "resolution_source": "platform_discovery",
                "access_mode": "sdk",
                "write_capability": "native_ready",
                "profile_confidence": 0.9,
            },
            "method_profile": {
                "method_kind": "judge",
                "input_shape": "string",
                "assertion_style": "judge",
                "uses_judge": True,
                "supports_multi_assert": False,
                "evaluator_scope": "dataset_bound",
                "execution_surface": "sdk_experiment",
                "binding_candidates": [
                    {
                        "binding_id": "helpfulness-v1",
                        "label": "helpfulness-v1",
                        "scope": "dataset_bound",
                        "execution_surface": "sdk_experiment",
                        "source": "sample_metadata",
                        "mapping_hints": {"input": "question", "reference_output": "answer"},
                        "reusable": True,
                        "confidence": 0.93,
                    }
                ],
                "supports_evaluator_reuse": True,
                "renderability_status": "native_ready",
                "confidence": 0.9,
            },
            "samples": [],
        }
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": "intent-judge",
            "gap_id": "gap-judge",
            "target_id": "langsmith::demo",
            "method_kind": "judge",
            "intent_type": "judge_calibration_probe",
            "title": "Helpful answer",
            "is_conversational": False,
            "input": "How do I reset my password?",
            "input_format": "string",
            "behavior_under_test": "Answer is helpful and accurate.",
            "pass_criteria": "The answer is helpful and accurate.",
            "failure_mode": "The answer is vague or wrong.",
            "probe_rationale": "Align to the existing helpfulness judge.",
            "related_risk_flag": "Helpful answers may degrade.",
            "preferred_evaluator_binding": "helpfulness-v1",
            "specificity_confidence": 0.9,
            "testability_confidence": 0.92,
            "novelty_confidence": 0.8,
            "realism_confidence": 0.85,
            "target_fit_confidence": 0.94,
        }
    )

    plan = build_evaluator_plan(
        intent,
        resolved_target=target,
        evaluator_config=EvalEvaluatorConfig(),
    )

    assert plan.action == "reuse_existing"
    assert plan.binding_ref == "helpfulness-v1"


def test_build_evaluator_dossiers_tracks_supporting_cases() -> None:
    sample = EvalCaseSnapshot.model_validate(
        {
            "case_id": "case-1",
            "source_platform": "promptfoo",
            "source_target_id": "promptfoo::demo",
            "source_target_name": "demo",
            "target_locator": "promptfooconfig.yaml",
            "method_kind": "judge",
            "native_case": {
                "vars": {"messages": [{"role": "user", "content": "hi"}]},
                "assert": [{"type": "llm-rubric", "value": "Friendly reply."}],
            },
            "native_input": [{"role": "user", "content": "hi"}],
            "native_output": {"assert": [{"type": "llm-rubric", "value": "Friendly reply."}]},
            "native_assertions": [
                {
                    "assertion_id": "case-1:0",
                    "assertion_kind": "judge",
                    "operator": "llm-rubric",
                    "rubric": "Friendly reply.",
                }
            ],
            "method_confidence": 0.85,
        }
    )
    profile = infer_method_profile("promptfoo", [sample])

    dossiers = build_evaluator_dossiers(
        "promptfoo",
        target_id="promptfoo::demo",
        samples=[sample],
        method_profile=profile,
    )

    assert dossiers[0].dossier_id == "promptfoo::demo::evaluator::01"
    assert dossiers[0].supporting_case_ids == ["case-1"]
    assert dossiers[0].explicitness == "explicit"


def test_build_evaluator_plan_requires_verification_when_enabled() -> None:
    target = ResolvedEvalTarget.model_validate(
        {
            "profile": {
                "target_id": "langsmith::demo",
                "platform": "langsmith",
                "locator": "demo-dataset",
                "target_name": "demo-dataset",
                "resolution_source": "platform_discovery",
                "access_mode": "sdk",
                "write_capability": "native_ready",
                "profile_confidence": 0.9,
            },
            "method_profile": {
                "method_kind": "judge",
                "input_shape": "string",
                "assertion_style": "judge",
                "uses_judge": True,
                "supports_multi_assert": False,
                "evaluator_scope": "dataset_bound",
                "execution_surface": "sdk_experiment",
                "binding_candidates": [
                    {
                        "binding_id": "helpfulness-v1",
                        "label": "helpfulness-v1",
                        "scope": "dataset_bound",
                        "execution_surface": "sdk_experiment",
                        "source": "sample_metadata",
                        "discovery_mode": "inferred",
                        "binding_status": "attached",
                        "verification_status": "unverified",
                        "reusable": True,
                        "confidence": 0.93,
                    }
                ],
                "supports_evaluator_reuse": True,
                "renderability_status": "native_ready",
                "confidence": 0.9,
            },
            "evaluator_dossiers": [
                {
                    "dossier_id": "langsmith::demo::evaluator::01",
                    "target_id": "langsmith::demo",
                    "binding_id": "helpfulness-v1",
                    "label": "helpfulness-v1",
                    "scope": "dataset_bound",
                    "execution_surface": "sdk_experiment",
                    "source": "sample_metadata",
                    "discovery_mode": "inferred",
                    "binding_status": "attached",
                    "verification_status": "unverified",
                    "explicitness": "explicit",
                    "reuse_feasibility": "confirmed",
                    "confidence": 0.93,
                    "rationale": "Recovered from sample metadata.",
                }
            ],
            "samples": [],
        }
    )
    intent = ProbeIntent.model_validate(
        {
            "intent_id": "intent-verify",
            "gap_id": "gap-verify",
            "target_id": "langsmith::demo",
            "method_kind": "judge",
            "intent_type": "judge_calibration_probe",
            "title": "Helpful answer",
            "is_conversational": False,
            "input": "How do I reset my password?",
            "input_format": "string",
            "behavior_under_test": "Answer is helpful and accurate.",
            "pass_criteria": "The answer is helpful and accurate.",
            "failure_mode": "The answer is vague or wrong.",
            "probe_rationale": "Align to the discovered helpfulness judge.",
            "related_risk_flag": "Helpful answers may degrade.",
            "evaluator_dossier_id": "langsmith::demo::evaluator::01",
            "preferred_evaluator_binding": "helpfulness-v1",
            "specificity_confidence": 0.9,
            "testability_confidence": 0.92,
            "novelty_confidence": 0.8,
            "realism_confidence": 0.85,
            "target_fit_confidence": 0.94,
        }
    )

    plan = build_evaluator_plan(
        intent,
        resolved_target=target,
        evaluator_config=EvalEvaluatorConfig(require_binding_verification=True),
    )

    assert plan.action == "manual"
    assert plan.evaluator_dossier_id == "langsmith::demo::evaluator::01"
