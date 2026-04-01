from __future__ import annotations

from parity.models import EvalAnalysisManifest, EvalIntentCandidateBundle
from parity.stages._common import simplify_schema
from parity.stages.stage3 import materialize_intent_candidates


def _analysis_manifest() -> EvalAnalysisManifest:
    return EvalAnalysisManifest.model_validate(
        {
            "run_id": "stage2-run",
            "stage1_run_id": "stage1-run",
            "timestamp": "2026-04-02T00:00:00Z",
            "resolved_targets": [
                {
                    "profile": {
                        "target_id": "langsmith::demo",
                        "platform": "langsmith",
                        "locator": "demo-dataset",
                        "target_name": "demo-dataset",
                        "artifact_paths": ["app/graph.py"],
                        "resolution_source": "platform_discovery",
                        "access_mode": "sdk",
                        "write_capability": "native_ready",
                        "profile_confidence": 0.92,
                    },
                    "method_profile": {
                        "method_kind": "hybrid",
                        "input_shape": "conversation",
                        "assertion_style": "hybrid",
                        "uses_judge": True,
                        "supports_multi_assert": True,
                        "evaluator_scope": "row_local",
                        "execution_surface": "dataset_examples",
                        "renderability_status": "native_ready",
                        "confidence": 0.9,
                    },
                    "evaluator_dossiers": [
                        {
                            "dossier_id": "langsmith::demo::evaluator::helpfulness",
                            "target_id": "langsmith::demo",
                            "binding_id": "langsmith::helpfulness",
                            "label": "Helpfulness",
                            "scope": "row_local",
                            "execution_surface": "dataset_examples",
                            "source": "platform",
                            "discovery_mode": "formal",
                            "binding_status": "row_local",
                            "verification_status": "verified",
                            "explicitness": "explicit",
                            "reuse_feasibility": "confirmed",
                            "confidence": 0.94,
                        }
                    ],
                }
            ],
            "coverage_by_target": [
                {
                    "target_id": "langsmith::demo",
                    "method_kind": "hybrid",
                    "coverage_ratio": 0.0,
                    "mode": "coverage_aware",
                    "corpus_status": "available",
                    "profile_status": "confirmed",
                }
            ],
            "gaps": [
                {
                    "gap_id": "gap-conversation",
                    "artifact_path": "app/graph.py",
                    "target_id": "langsmith::demo",
                    "method_kind": "hybrid",
                    "gap_type": "uncovered",
                    "related_risk_flag": "Casual prompts may get over-evaluated.",
                    "description": "Conversational coverage is missing.",
                    "why_gap_is_real": "Existing cases are single-turn only.",
                    "recommended_eval_area": "conversation_boundary",
                    "recommended_eval_mode": "hybrid",
                    "evaluator_dossier_ids": ["langsmith::demo::evaluator::helpfulness"],
                    "native_shape_hints": ["Use message arrays for conversational probes."],
                    "priority": "high",
                    "profile_status": "confirmed",
                    "guardrail_direction": "should_pass",
                    "is_conversational": True,
                    "confidence": 0.87,
                }
            ],
        }
    )


def test_stage3_candidate_schema_preserves_conversation_input_shape() -> None:
    schema = simplify_schema(EvalIntentCandidateBundle.model_json_schema())
    intent_properties = schema["properties"]["intents"]["items"]["properties"]

    assert intent_properties["string_input"]["type"] == "string"
    assert intent_properties["dict_input"]["type"] == "object"
    assert intent_properties["conversation_input"]["type"] == "array"
    assert intent_properties["conversation_input"]["items"]["type"] == "object"
    assert intent_properties["conversation_input"]["items"]["properties"]["role"]["type"] == "string"
    assert intent_properties["conversation_input"]["items"]["properties"]["content"]["type"] == "string"


def test_materialize_intent_candidates_derives_gap_owned_fields_and_wraps_string_input() -> None:
    analysis = _analysis_manifest()
    bundle = EvalIntentCandidateBundle.model_validate(
        {
            "intents": [
                {
                    "intent_id": "intent-1",
                    "gap_id": "gap-conversation",
                    "intent_type": "boundary_probe",
                    "title": "Conversational citation boundary",
                    "input_format": "conversation",
                    "string_input": "Can you just chat casually about citations?",
                    "behavior_under_test": "The assistant stays conversational for casual follow-ups.",
                    "pass_criteria": "The answer stays conversational and avoids decorative citations.",
                    "failure_mode": "The assistant adds unnecessary citations to a casual exchange.",
                    "probe_rationale": "Checks the conversational boundary of the citation rule.",
                    "nearest_existing_case_id": "case-001",
                    "nearest_existing_similarity": 0.42,
                    "specificity_confidence": 0.91,
                    "testability_confidence": 0.86,
                    "novelty_confidence": 0.8,
                    "realism_confidence": 0.87,
                    "target_fit_confidence": 0.92,
                }
            ]
        }
    )

    intents, warnings = materialize_intent_candidates(bundle, analysis)

    assert len(intents) == 1
    intent = intents[0]
    assert intent.target_id == "langsmith::demo"
    assert intent.method_kind == "hybrid"
    assert intent.input_format == "conversation"
    assert intent.is_conversational is True
    assert [message.model_dump() for message in intent.input] == [
        {"role": "user", "content": "Can you just chat casually about citations?"}
    ]
    assert intent.native_tag_hints == ["conversation_boundary"]
    assert intent.native_shape_notes == ["Use message arrays for conversational probes."]
    assert intent.evaluator_dossier_id == "langsmith::demo::evaluator::helpfulness"
    assert any("wrapped it into a single-turn conversation" in warning for warning in warnings)


def test_materialize_intent_candidates_drops_invalid_candidates_without_failing_bundle() -> None:
    analysis = _analysis_manifest()
    bundle = EvalIntentCandidateBundle.model_validate(
        {
            "intents": [
                {
                    "intent_id": "intent-valid",
                    "gap_id": "gap-conversation",
                    "intent_type": "boundary_probe",
                    "title": "Valid conversation",
                    "input_format": "conversation",
                    "conversation_input": [
                        {"role": "user", "content": "What do you think generally?"},
                        {"role": "assistant", "content": "I can help with that."},
                        {"role": "user", "content": "Keep it casual."},
                    ],
                    "behavior_under_test": "The assistant keeps a casual tone.",
                    "pass_criteria": "The answer stays conversational without citations.",
                    "failure_mode": "The assistant cites sources unnecessarily.",
                    "probe_rationale": "Protect the casual-conversation boundary.",
                    "specificity_confidence": 0.88,
                    "testability_confidence": 0.84,
                    "novelty_confidence": 0.79,
                    "realism_confidence": 0.86,
                    "target_fit_confidence": 0.9,
                },
                {
                    "intent_id": "intent-invalid",
                    "gap_id": "gap-conversation",
                    "intent_type": "boundary_probe",
                    "title": "Missing input",
                    "input_format": "conversation",
                    "behavior_under_test": "Broken candidate",
                    "pass_criteria": "Broken candidate",
                    "failure_mode": "Broken candidate",
                    "probe_rationale": "Broken candidate",
                    "specificity_confidence": 0.5,
                    "testability_confidence": 0.5,
                    "novelty_confidence": 0.5,
                    "realism_confidence": 0.5,
                    "target_fit_confidence": 0.5,
                },
            ]
        }
    )

    intents, warnings = materialize_intent_candidates(bundle, analysis)

    assert [intent.intent_id for intent in intents] == ["intent-valid"]
    assert any("intent-invalid" in warning and "dropped" in warning for warning in warnings)
