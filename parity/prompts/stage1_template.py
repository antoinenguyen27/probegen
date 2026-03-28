from __future__ import annotations

import json

from parity.context import truncate_text

STAGE1_SYSTEM_TEMPLATE = """You are a behavioral change analyst for LLM-based agent systems.

PRODUCT CONTEXT:
{product_context}

KNOWN FAILURE MODES:
{bad_examples}

PR METADATA:
{pr_metadata_json}

ALL CHANGED FILES IN THIS PR:
{all_changed_files_json}
(These are all files modified, added, or deleted in this PR.)

HINT-PATTERN MATCHES — PRE-LOADED:
{hint_matched_artifacts_json}
(These files match configured hint patterns and have been pre-loaded with before/after content and diffs for efficiency.)

CONFIGURED HINT PATTERNS:
{hint_patterns_json}
(These are the patterns your team configured as hints — not a complete list. Agent behavior changes may appear in files that don't match any pattern.)

PROCESS:
1. Start with the pre-loaded hint-pattern matches. Analyze each for behavioral significance.
2. Review ALL CHANGED FILES. For any file not pre-loaded that could be behaviorally significant,
   fetch and inspect it:
     - Read the file:          Read tool on the current path
     - Get before content:     Bash: git show origin/{base_branch}:<path>
     - Get the diff:           Bash: git diff --unified=5 origin/{base_branch}...HEAD -- <path>
3. Behavioral artifacts include (not limited to):
     - System prompts, instructions, agent personas (any format: .md, .txt, .yaml, .json, .j2, Python constants)
     - Tool descriptions, tool schemas, function calling configs
     - LLM judges, rubrics, graders, evaluators
     - Output validators, classifiers, filters, guardrails, safety configs
     - Retrieval instructions, reranking configs, router prompts
     - Retry policies, fallback prompts, escalation logic
     - Any file whose change alters what the LLM agent does or decides
4. For Python files: look for module-level string assignments (constants) that contain prompt-like content.
   Hint: {python_patterns_hint}
5. Classify each behavioral artifact you discover using these artifact_type values:
     system_prompt | tool_description | llm_judge | input_classifier | output_classifier |
     tool_validator | safety_classifier | retrieval_instruction | planner_prompt |
     output_schema | schema_validator | retry_policy | fallback_prompt | unknown
6. Infer the intended change for each artifact. Compare against PR description. Flag contradictions.
7. Identify unintended risks, including guardrail false-negative and false-positive shifts.
8. IF YOU FOUND NO BEHAVIORAL CHANGES (no artifacts in your analysis):
   - Set has_changes to false
   - Set overall_risk to "low"
   - Set changes array to empty []
   - Set compound_change_detected to false
   - This is a valid, complete response. Return it as JSON.
9. Output BehaviorChangeManifest JSON only. No prose.
"""


def render_stage1_prompt(raw_change_data: dict, context) -> str:
    pr_metadata = {
        "pr_number": raw_change_data.get("pr_number"),
        "pr_title": raw_change_data.get("pr_title"),
        "pr_body": raw_change_data.get("pr_body"),
        "pr_labels": raw_change_data.get("pr_labels"),
        "base_branch": raw_change_data.get("base_branch"),
        "head_sha": raw_change_data.get("head_sha"),
        "repo_full_name": raw_change_data.get("repo_full_name"),
    }
    hint_patterns = raw_change_data.get("hint_patterns", {})
    python_patterns = [
        *hint_patterns.get("behavior_python_patterns", []),
        *hint_patterns.get("guardrail_python_patterns", []),
    ]
    base_branch = raw_change_data.get("base_branch", "main")
    return STAGE1_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 4000),
        bad_examples=truncate_text(context.bad_examples, 4000),
        pr_metadata_json=json.dumps(pr_metadata, indent=2),
        all_changed_files_json=json.dumps(raw_change_data.get("all_changed_files", []), indent=2),
        hint_matched_artifacts_json=json.dumps(raw_change_data.get("hint_matched_artifacts", []), indent=2),
        hint_patterns_json=json.dumps(hint_patterns, indent=2),
        base_branch=base_branch,
        python_patterns_hint=", ".join(python_patterns) if python_patterns else "e.g. *_prompt, *_instruction, system_*",
    )
