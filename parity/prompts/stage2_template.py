from __future__ import annotations

import json
from copy import deepcopy

STAGE2_SYSTEM_TEMPLATE = """You are a coverage gap analyst for LLM-based agent evaluation suites.

MANIFEST:
{manifest_json}

PROCESS:
1. Retrieve relevant eval cases using MCP tools or file-based fallbacks.
2. If you retrieve one or more existing eval cases, call `parity embed-batch` to embed them.
3. If you have embedded cases, call `parity find-similar` for each risk flag or predicted impact.
4. If you retrieve real eval cases by any method, remain in coverage-aware mode:
   - set `coverage_summary.mode` to `coverage_aware`
   - set `coverage_summary.corpus_status` to `available`
   - if the retrieval path matters (for example, file-based fallback rather than MCP), explain it in
     `coverage_summary.retrieval_notes`
   - omit `coverage_summary.bootstrap_reason`
5. If no relevant eval cases exist at all, switch to bootstrap mode:
   - set `coverage_summary.mode` to `bootstrap`
   - set `coverage_summary.corpus_status` to `empty` or `unavailable`
   - explain why in `coverage_summary.bootstrap_reason`
   - omit `coverage_summary.retrieval_notes` unless it adds material context
   - classify the predicted risks as uncovered baseline gaps seeded from the diff, system prompt,
     guardrails, and available business context rather than corpus comparison
   - leave `nearest_existing_cases` empty for those gaps
6. `coverage_summary.bootstrap_reason` is bootstrap-only. Never populate it when mode is `coverage_aware`,
   including when coverage is found via file-based fallback.
7. Classify each risk flag as covered, boundary_shift, or uncovered.
8. Output CoverageGapManifest JSON only. Do not generate probes.
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
