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


def test_stage2_prompt_includes_bootstrap_instructions() -> None:
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
        mapping_resolutions=[
            {
                "artifact_path": "prompts/answer.md",
                "mapping_status": "explicit",
                "platform": "langsmith",
                "target": "answer-regression",
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

    assert "switch to bootstrap mode" in prompt
    assert "leave `nearest_existing_cases` empty" in prompt
    assert "remain in coverage-aware mode" in prompt
    assert "`coverage_summary.retrieval_notes`" in prompt
    assert "Never populate it when mode is `coverage_aware`" in prompt
    assert "RESOLVED DATASET MAPPINGS" in prompt
    assert "BOOTSTRAP BRIEF" in prompt
    assert "Do not inspect `parity.yaml`" in prompt
    assert "preferred starting point, not as infallible ground truth" in prompt
    assert "limited platform-side discovery" in prompt
    assert "Record that recovery in" in prompt
    assert "`search_eval_targets`" in prompt
    assert "`fetch_eval_cases`" in prompt
    assert "`embed_batch`" in prompt
    assert "`budget_exceeded: true`" in prompt
    assert "returned cached embeddings" in prompt
    assert "`find_similar_batch`" in prompt
    assert "do not flatten unrelated artifacts or unrelated datasets into one batch" in prompt
    assert '"target": "answer-regression"' in prompt


def test_stage3_prompt_describes_bootstrap_mode() -> None:
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
            "coverage_summary": {
                "total_relevant_cases": 0,
                "cases_covering_changed_behavior": 0,
                "coverage_ratio": 0.0,
                "mode": "bootstrap",
                "corpus_status": "empty",
                "bootstrap_reason": "No existing eval cases were found for this agent.",
            },
            "gaps": [
                {
                    "gap_id": "gap_001",
                    "artifact_path": "prompts/answer.md",
                    "gap_type": "uncovered",
                    "related_risk_flag": "Casual questions may receive citations unexpectedly",
                    "description": "No baseline coverage exists yet.",
                    "nearest_existing_cases": [],
                    "priority": "high",
                    "guardrail_direction": None,
                    "is_conversational": False,
                }
            ],
        },
        context,
        proposal_probe_limit=4,
        candidate_probe_pool_limit=10,
    )

    assert "COVERAGE SUMMARY" in prompt
    assert '"mode": "bootstrap"' in prompt
    assert "there is no usable eval corpus for comparison" in prompt
    assert "Generate up to 10 candidate probes" in prompt
    assert "keep at most 4 final proposal probes" in prompt
    assert "Return the full candidate pool in `probes`" in prompt
