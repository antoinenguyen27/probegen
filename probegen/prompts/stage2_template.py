from __future__ import annotations

import json
from copy import deepcopy

STAGE2_SYSTEM_TEMPLATE = """You are a coverage gap analyst for LLM-based agent evaluation suites.

MANIFEST:
{manifest_json}

PROCESS:
1. Retrieve relevant eval cases using MCP tools or file-based fallbacks.
2. Call `probegen embed-batch` to embed retrieved cases.
3. Call `probegen find-similar` for each risk flag or predicted impact.
4. Classify each risk flag as covered, boundary_shift, or uncovered.
5. Output CoverageGapManifest JSON only. Do not generate probes.
"""


def strip_raw_diffs(stage1_manifest: dict) -> dict:
    stripped = deepcopy(stage1_manifest)
    for change in stripped.get("changes", []):
        change.pop("raw_diff", None)
        change.pop("before_content", None)
        change.pop("after_content", None)
    return stripped


def render_stage2_prompt(stage1_manifest: dict) -> str:
    stripped = strip_raw_diffs(stage1_manifest)
    return STAGE2_SYSTEM_TEMPLATE.format(manifest_json=json.dumps(stripped, indent=2))
