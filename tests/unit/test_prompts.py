from __future__ import annotations

from pathlib import Path

from parity.context import ContextPack
from parity.prompts.stage1_template import render_stage1_prompt
from parity.prompts.stage2_template import render_stage2_prompt
from parity.prompts.stage3_template import render_stage3_prompt


def test_stage1_prompt_allows_unchanged_supporting_file_reads() -> None:
    prompt = render_stage1_prompt(
        {
            "pr_number": 7,
            "pr_title": "Update router prompt",
            "pr_body": "",
            "pr_labels": [],
            "base_branch": "main",
            "head_sha": "abc123",
            "repo_full_name": "acme/repo",
            "all_changed_files": [{"path": "app/router.py"}],
            "hint_matched_artifacts": [],
            "hint_patterns": {},
        },
        ContextPack(product="Agent product", bad_examples="Known failures"),
    )

    assert "Inspect unchanged supporting files when needed" in prompt
    assert "Use Read and Glob to follow imports" in prompt
    assert "artifact_path` must be the repo-relative file path only" in prompt
    assert "Never append symbol selectors like `::GENERATE_PROMPT`" in prompt


def test_stage2_prompt_includes_topology_discovery_instructions() -> None:
    prompt = render_stage2_prompt(
        {
            "run_id": "stage1-run",
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "inferred_intent": "Require citations for factual answers",
                }
            ],
        },
        rule_resolutions=[
            {
                "artifact_path": "prompts/answer.md",
                "rule_status": "explicit",
                "preferred_platform": "langsmith",
                "preferred_target": "answer-regression",
            }
        ],
        bootstrap_brief={
            "overall_risk": "high",
            "compound_change_detected": False,
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "risk_flags": ["Casual questions may receive citations unexpectedly"],
                }
            ],
        },
    )

    assert "discover_eval_targets" in prompt
    assert "fetch_eval_target_snapshot" in prompt
    assert "discover_target_evaluators" in prompt
    assert "read_evaluator_binding" in prompt
    assert "verify_evaluator_binding" in prompt
    assert "discover_repo_eval_assets" in prompt
    assert "list_platform_evaluator_capabilities" in prompt
    assert "Preserve the native case shape" in prompt
    assert "Discover evaluator regime" in prompt
    assert "output evalanalysismanifest json only" in prompt.lower()
    assert '"preferred_target": "answer-regression"' in prompt


def test_stage3_prompt_describes_native_eval_synthesis() -> None:
    context = ContextPack(
        product="Acme assistant for support questions.",
        users="Support agents and end users.",
        interactions="Users ask factual and conversational follow-ups.",
        good_examples="Clear grounded answers.",
        bad_examples="Decorative citations on casual replies.",
        traces_dir=Path("/nonexistent"),
        trace_max_samples=5,
    )
    prompt = render_stage3_prompt(
        {
            "run_id": "stage1-run",
            "overall_risk": "high",
            "compound_change_detected": False,
            "changes": [
                {
                    "artifact_path": "prompts/answer.md",
                    "inferred_intent": "Require citations for factual answers",
                    "unintended_risk_flags": ["Casual questions may receive citations unexpectedly"],
                    "affected_components": ["app/graph.py"],
                }
            ],
        },
        {
            "resolved_targets": [
                {
                    "profile": {
                        "target_id": "promptfoo::evals/promptfooconfig.yaml",
                        "platform": "promptfoo",
                        "locator": "evals/promptfooconfig.yaml",
                        "target_name": "evals/promptfooconfig.yaml",
                        "artifact_paths": ["prompts/answer.md"],
                        "resolution_source": "config_rule",
                        "access_mode": "file",
                        "write_capability": "native_ready",
                        "profile_confidence": 0.9
                    },
                    "method_profile": {
                        "method_kind": "hybrid",
                        "renderability_status": "native_ready",
                        "supports_multi_assert": True,
                        "evaluator_scope": "row_local",
                        "execution_surface": "config_file",
                        "binding_candidates": [{"binding_id": "promptfoo::llm-rubric"}]
                    },
                    "resolution_notes": ["Matched configured promptfoo target."]
                }
            ],
            "coverage_by_target": [
                {
                    "target_id": "promptfoo::evals/promptfooconfig.yaml",
                    "method_kind": "hybrid",
                    "coverage_ratio": 0.0,
                    "mode": "coverage_aware",
                    "corpus_status": "available",
                    "profile_status": "confirmed",
                }
            ],
            "gaps": [
                {
                    "gap_id": "gap_001",
                    "target_id": "promptfoo::evals/promptfooconfig.yaml",
                    "artifact_path": "prompts/answer.md",
                    "method_kind": "hybrid",
                    "gap_type": "uncovered",
                    "related_risk_flag": "Casual questions may receive citations unexpectedly",
                    "description": "No native coverage exists yet.",
                    "why_gap_is_real": "Existing promptfoo cases only cover factual citation behavior.",
                    "recommended_eval_area": "conversation_boundary",
                    "recommended_eval_mode": "hybrid",
                    "native_shape_hints": [
                        "Use vars.messages for multi-turn probes."
                    ],
                    "compatible_nearest_cases": [],
                    "repo_asset_refs": [],
                    "priority": "high",
                    "profile_status": "confirmed",
                    "guardrail_direction": None,
                    "is_conversational": True,
                    "confidence": 0.84
                }
            ],
        },
        context,
        proposal_limit=4,
        candidate_intent_pool_limit=10,
    )

    assert "STAGE 2 ANALYSIS SUMMARY" in prompt
    assert "HOST-OWNED EVIDENCE TOOLS" in prompt
    assert "list_evaluator_dossiers" in prompt
    assert "read_evaluator_dossier" in prompt
    assert "native-feeling row attributes" in prompt
    assert "Populate exactly one of `string_input`, `dict_input`, or `conversation_input`." in prompt
    assert "The host will derive `target_id`, `method_kind`, `related_risk_flag`" in prompt
    assert "Prefer `conversation_input` over `string_input` for conversational gaps." in prompt
    assert "Generate up to 10 candidate intents" in prompt
    assert "keep at most 4 final intents" in prompt
    assert "Output EvalIntentCandidateBundle JSON only" in prompt
