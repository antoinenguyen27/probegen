from __future__ import annotations

from parity.errors import BudgetExceededError
from parity.config import ParityConfig
from parity.stages._common import format_tool_summary
from parity.stages.stage2 import (
    _build_stage2_bootstrap_brief,
    _build_stage2_budget_fallback,
    _build_stage2_degraded_reason,
    _build_stage2_rule_resolutions,
)


def test_build_stage2_rule_resolutions_uses_explicit_rule() -> None:
    config = ParityConfig.model_validate(
        {
            "evals": {
                "rules": [
                    {
                        "artifact": "prompts/**",
                        "preferred_platform": "langsmith",
                        "preferred_target": "citation-agent-evals",
                        "allowed_methods": ["judge", "hybrid"],
                        "preferred_methods": ["hybrid"],
                    }
                ]
            }
        }
    )

    resolutions = _build_stage2_rule_resolutions(
        {
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "artifact_class": "system_prompt",
                }
            ]
        },
        config,
    )

    assert resolutions == [
        {
            "artifact_path": "prompts/answer.md",
            "artifact_class": "system_prompt",
            "rule_status": "explicit",
            "preferred_platform": "langsmith",
            "preferred_target": "citation-agent-evals",
            "preferred_project": None,
            "allowed_methods": ["judge", "hybrid"],
            "preferred_methods": ["hybrid"],
            "repo_asset_hints": [],
            "discovery_order": ["langsmith", "promptfoo"],
        }
    ]


def test_build_stage2_rule_resolutions_marks_unmapped_artifact_unresolved() -> None:
    config = ParityConfig()

    resolutions = _build_stage2_rule_resolutions(
        {"changes": [{"artifact_path": "prompts/answer.md"}]},
        config,
    )

    assert resolutions[0]["rule_status"] == "unresolved"
    assert resolutions[0]["preferred_target"] is None
    assert "promptfoo" in resolutions[0]["discovery_order"]


def test_build_stage2_bootstrap_brief_dedupes_risk_flags() -> None:
    brief = _build_stage2_bootstrap_brief(
        {
            "overall_risk": "high",
            "compound_change_detected": True,
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "artifact_class": "system_prompt",
                    "inferred_intent": "Require citations",
                    "change_summary": "Require citations on factual answers.",
                    "affected_components": ["app/graph.py"],
                    "unintended_risk_flags": ["Casual replies may gain citations", "Casual replies may gain citations"],
                    "false_negative_risks": ["Missing citations on factual claims"],
                    "false_positive_risks": ["Casual replies may gain citations"],
                    "behavioral_signatures": ["cite sources", "factual answer"],
                    "changed_entities": [{"entity_kind": "prompt", "name": "citation_agent", "operation": "modified"}],
                    "observable_delta": {"after_behavior": "Factual answers now require citations."},
                    "eval_search_hints": ["citation requirement", "factual answer with sources"],
                    "validation_focus": ["judge", "conversation"],
                    "evidence_snippets": [{"label": "prompt", "summary": "Added citation requirement."}],
                }
            ],
        }
    )

    assert brief == {
        "overall_risk": "high",
        "compound_change_detected": True,
        "changes": [
            {
                "artifact_path": "prompts/answer.md",
                "artifact_class": "system_prompt",
                "inferred_intent": "Require citations",
                "change_summary": "Require citations on factual answers.",
                "affected_components": ["app/graph.py"],
                "risk_flags": [
                    "Casual replies may gain citations",
                    "Missing citations on factual claims",
                ],
                "behavioral_signatures": ["cite sources", "factual answer"],
                "changed_entities": [{"entity_kind": "prompt", "name": "citation_agent", "operation": "modified"}],
                "observable_delta": {"after_behavior": "Factual answers now require citations."},
                "eval_search_hints": ["citation requirement", "factual answer with sources"],
                "validation_focus": ["judge", "conversation"],
                "evidence_snippets": [{"label": "prompt", "summary": "Added citation requirement."}],
            }
        ],
    }


def test_format_tool_summary_includes_counts_and_durations() -> None:
    summary = format_tool_summary({"Read": 2, "Bash": 1}, {"Read": 150, "Bash": 25})
    assert summary == "Bash x1 (~25ms), Read x2 (~150ms)"


def test_stage2_budget_fallback_bootstraps_targets_from_partial_state() -> None:
    manifest = _build_stage2_budget_fallback(
        stage1_manifest={
            "run_id": "stage1-run",
            "overall_risk": "medium",
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "change_summary": "Require citations",
                    "unintended_risk_flags": [],
                    "false_negative_risks": [],
                    "false_positive_risks": [],
                }
            ],
        },
        run_id="stage2-run",
        timestamp="2026-03-30T00:00:00Z",
        runtime_metadata={
            "retrieval": {
                "fetch_request_count": 0,
                "total_cases": 0,
                "sources": [],
            },
            "embedding": {
                "blocked_request_count": 0,
            },
        },
        reason="Agent spend cap was exhausted.",
    )

    assert manifest.resolved_targets[0].profile.platform == "bootstrap"
    assert manifest.resolved_targets[0].method_profile.renderability_status == "review_only"
    assert manifest.analysis_status == "degraded"
    assert manifest.degradation_reason == "Agent spend cap was exhausted."
    assert manifest.unresolved_artifacts == ["prompts/answer.md"]
    assert manifest.runtime_metadata["retrieval"]["fetch_request_count"] == 0


def test_stage2_budget_fallback_preserves_cached_native_targets() -> None:
    manifest = _build_stage2_budget_fallback(
        stage1_manifest={
            "run_id": "stage1-run",
            "overall_risk": "medium",
            "changes": [
                {
                    "artifact_path": "app/graph.py",
                    "artifact_class": "application_logic",
                    "change_summary": "Require grounded attribution",
                    "validation_focus": ["judge", "retrieval"],
                    "unintended_risk_flags": ["Fabricated source attribution"],
                    "false_negative_risks": [],
                    "false_positive_risks": [],
                }
            ],
        },
        run_id="stage2-run",
        timestamp="2026-03-30T00:00:00Z",
        runtime_metadata={
            "retrieval": {
                "fetch_request_count": 1,
                "total_cases": 1,
                "sources": [{"platform": "promptfoo", "target": "evals/promptfooconfig.yaml"}],
            },
            "embedding": {
                "blocked_request_count": 0,
            },
        },
        reason="Stage 2 spend cap was exhausted.",
        cached_target_snapshots=[
            {
                "target_id": "promptfoo::evals/promptfooconfig.yaml",
                "platform": "promptfoo",
                "target": "evals/promptfooconfig.yaml",
                "target_name": "evals/promptfooconfig.yaml",
                "dataset_id": None,
                "project": None,
                "artifact_paths": ["app/graph.py"],
                "target_locator": "evals/promptfooconfig.yaml",
                "sample_count": 1,
                "samples": [
                    {
                        "case_id": "case_001",
                        "source_platform": "promptfoo",
                        "source_target_id": "promptfoo::evals/promptfooconfig.yaml",
                        "source_target_name": "evals/promptfooconfig.yaml",
                        "target_locator": "evals/promptfooconfig.yaml",
                        "project": None,
                        "method_kind": "deterministic",
                        "native_case": {"vars": {"query": "hi"}, "assert": [{"type": "contains", "value": "hello"}]},
                        "native_input": {"query": "hi"},
                        "native_output": "hello",
                        "normalized_projection": {
                            "input_text": "hi",
                            "expected_text": "hello",
                            "comparison_text": "hi\n\nhello",
                            "is_conversational": False,
                        },
                        "native_assertions": [
                            {
                                "assertion_id": "assert_001",
                                "assertion_kind": "deterministic",
                                "expected_value": "hello",
                            }
                        ],
                        "method_hints": ["promptfoo_assert"],
                        "method_confidence": 0.82,
                        "metadata": {},
                        "tags": [],
                    }
                ],
                "method_profile": {
                    "method_kind": "deterministic",
                    "input_shape": "dict",
                    "assertion_style": "deterministic",
                    "uses_judge": False,
                    "supports_multi_assert": False,
                    "evaluator_binding": None,
                    "evaluator_scope": "row_local",
                    "execution_surface": "config_file",
                    "binding_candidates": [],
                    "supports_evaluator_reuse": False,
                    "formal_discovery_status": "confirmed",
                    "formal_binding_count": 0,
                    "metadata_conventions": {},
                    "renderability_status": "native_ready",
                    "confidence": 0.82,
                    "notes": [],
                },
                "evaluator_dossiers": [],
                "aggregate_method_hints": ["promptfoo_assert"],
                "raw_field_patterns": ["assert:contains"],
                "profile_confidence": 0.82,
            }
        ],
    )

    assert manifest.resolved_targets[0].profile.platform == "promptfoo"
    assert manifest.resolved_targets[0].profile.target_id == "promptfoo::evals/promptfooconfig.yaml"
    assert manifest.resolved_targets[0].method_profile.method_kind == "deterministic"
    assert manifest.coverage_by_target[0].target_id == "promptfoo::evals/promptfooconfig.yaml"
    assert manifest.gaps[0].target_id == "promptfoo::evals/promptfooconfig.yaml"
    assert manifest.gaps[0].profile_status == "uncertain"
    assert manifest.unresolved_artifacts == []


def test_stage2_degraded_reason_distinguishes_budget_and_turns() -> None:
    assert (
        _build_stage2_degraded_reason(
            BudgetExceededError(
                "Cost budget exceeded",
                stage=2,
                details={"subtype": "error_max_budget_usd"},
            )
        )
        == "Stage 2 spend cap was exhausted before full eval analysis completed. Returning a degraded analysis manifest from recovered discovery evidence and bootstrap fallback where needed."
    )
    assert (
        _build_stage2_degraded_reason(
            BudgetExceededError(
                "Max turns limit reached — increase max_turns or simplify the stage prompt",
                stage=2,
                details={"subtype": "error_max_turns"},
            )
        )
        == "Stage 2 max-turn limit was reached before full eval analysis completed. Returning a degraded analysis manifest from recovered discovery evidence and bootstrap fallback where needed."
    )
