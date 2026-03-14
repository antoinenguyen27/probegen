from __future__ import annotations

import json

from probegen.context import truncate_text

STAGE1_SYSTEM_TEMPLATE = """You are a behavioral change analyst for LLM-based agent systems.

PRODUCT CONTEXT:
{product_context}

KNOWN FAILURE MODES:
{bad_examples}

RAW CHANGE DATA:
{raw_change_data_json}

PROCESS:
1. Analyze the changed artifacts and infer intended behavioral changes.
2. Compare the inferred intent with the PR description and flag contradictions.
3. Identify unintended risks, including false-negative and false-positive guardrail risks.
4. Output BehaviorChangeManifest JSON only. No prose.
"""


def render_stage1_prompt(raw_change_data: dict, context) -> str:
    return STAGE1_SYSTEM_TEMPLATE.format(
        product_context=truncate_text(context.product, 4000),
        bad_examples=truncate_text(context.bad_examples, 4000),
        raw_change_data_json=json.dumps(raw_change_data, indent=2),
    )
