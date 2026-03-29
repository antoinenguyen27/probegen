from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

STAGE2_SYSTEM_TEMPLATE = """You are a coverage gap analyst for LLM-based agent evaluation suites.

MANIFEST:
{manifest_json}

RESOLVED DATASET MAPPINGS:
{mapping_resolutions_json}

BOOTSTRAP BRIEF:
{bootstrap_brief_json}

OPERATING CONSTRAINTS:
- Treat resolved dataset mappings as the preferred starting point, not as infallible ground truth.
- Do not inspect `parity.yaml` or search the repository to rediscover mappings.
- When `mapping_status` is `explicit`, query the specified `platform` and `target` first.
- If an explicit target is missing, inaccessible, empty, or appears materially unrelated to the changed behavior,
  you may perform limited platform-side discovery to find a better match.
- Prefer discovery within the same platform before considering any other configured platform.
- Only attempt convention-based dataset discovery immediately for artifacts whose `mapping_status` is `unresolved`.
- Use the bootstrap brief when no relevant eval cases exist; do not search the repository for additional product context in this stage.

PROCESS:
1. For each artifact with `mapping_status` `explicit`, retrieve eval cases from the specified platform/target using `fetch_eval_cases`.
2. If an explicit target fails validation because it is missing, inaccessible, empty, or materially unrelated, you may
   call `search_eval_targets` on that same platform to find a better match. Record that recovery in
   `coverage_summary.retrieval_notes`, then re-run `fetch_eval_cases` with the recovered target.
3. For each artifact with `mapping_status` `unresolved`, you may use `search_eval_targets` for limited same-platform discovery
   and then `fetch_eval_cases` to load the chosen corpus.
4. If you retrieve one or more existing eval cases, call `embed_batch` to embed them.
   - `embed_batch` may return `budget_exceeded: true`
   - when that happens, stop requesting more embeddings
   - you may still use any returned cached embeddings, but treat `missing_ids` as not embedded
   - continue with a degraded partial/bootstrap analysis instead of failing the stage
5. When comparing multiple risk flags or predicted impacts against the same resolved corpus, prefer
   `find_similar_batch` so you can evaluate that scoped slice in one pass while preserving
   per-candidate results. Use `find_similar` only when you truly have a single candidate.
6. If you have embedded cases, compare each semantically coherent slice separately:
   - keep one artifact or tightly related artifact slice per comparison batch
   - keep one resolved corpus per comparison batch
   - do not flatten unrelated artifacts or unrelated datasets into one batch
7. If you retrieve real eval cases by any method, remain in coverage-aware mode:
   - set `coverage_summary.mode` to `coverage_aware`
   - set `coverage_summary.corpus_status` to `available`
   - if the retrieval path matters (for example, file-based fallback rather than MCP), explain it in
     `coverage_summary.retrieval_notes`
   - omit `coverage_summary.bootstrap_reason`
8. If no relevant eval cases exist at all, switch to bootstrap mode:
   - set `coverage_summary.mode` to `bootstrap`
   - set `coverage_summary.corpus_status` to `empty` or `unavailable`
   - explain why in `coverage_summary.bootstrap_reason`
   - omit `coverage_summary.retrieval_notes` unless it adds material context
   - classify the predicted risks as uncovered baseline gaps seeded from the Stage 1 manifest,
     the bootstrap brief, and available mapping information rather than corpus comparison
   - leave `nearest_existing_cases` empty for those gaps
9. `coverage_summary.bootstrap_reason` is bootstrap-only. Never populate it when mode is `coverage_aware`,
   including when coverage is found via file-based fallback.
10. Classify each risk flag as covered, boundary_shift, or uncovered.
11. Output CoverageGapManifest JSON only. Do not generate probes.
"""


def strip_raw_diffs(stage1_manifest: dict) -> dict:
    stripped = deepcopy(stage1_manifest)
    for change in stripped.get("changes", []):
        change.pop("raw_diff", None)
        change.pop("before_content", None)
        change.pop("after_content", None)
    return stripped


def render_stage2_prompt(
    stage1_manifest: dict,
    *,
    mapping_resolutions: list[dict[str, Any]] | None = None,
    bootstrap_brief: dict[str, Any] | None = None,
) -> str:
    stripped = strip_raw_diffs(stage1_manifest)
    return STAGE2_SYSTEM_TEMPLATE.format(
        manifest_json=json.dumps(stripped, indent=2),
        mapping_resolutions_json=json.dumps(mapping_resolutions or [], indent=2),
        bootstrap_brief_json=json.dumps(bootstrap_brief or {}, indent=2),
    )
