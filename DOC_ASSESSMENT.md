# Probegen Docs & Spec Assessment

> Phase 1: Assessment only — no edits made to code or docs.
> Generated: 2026-03-19

## Legend
- **[WRONG]** — Implementation directly contradicts the doc claim
- **[STALE]** — Doc was accurate but hasn't caught up to code changes
- **[MISSING]** — Feature is documented but not implemented in code
- **[DX]** — Correct but bad developer experience (confusing, bloated, misplaced)

---

## Critical Issues (Doc ↔ Implementation Contradictions)

### 1. [WRONG] `get-behavior-diff` framing as an agent-called tool (Addendum Gap 4, §4 component table)

The addendum Gap 4 says `get-behavior-diff` is "Called by the Agent SDK as a Bash tool in Stage 1." The §4 component table lists it as a tool the agent calls. **This is false.** `run_stage.py` calls `build_raw_change_data()` in the orchestrator, before the agent session starts, and injects the result into the Stage 1 prompt. The Stage 1 agent never calls `probegen get-behavior-diff` — it receives the data pre-loaded and then uses `Read/Bash/Glob` to explore further. This framing is significantly misleading for anyone trying to understand Stage 1's mechanics.

### 2. [WRONG] Stage 3 `allowed_tools` (§8 Agent SDK Invocation block)

Spec §8 says Stage 3 uses `allowed_tools=["Bash"]` "only for find_similar on generated candidates." The implementation in `stage3.py` has `allowed_tools=[]` with comment: "Stage 3 is pure generation from prompt context; no tools needed." The diversity/ranking is done post-generation in orchestrator Python code (`rank_probes`, `apply_diversity_limit` in `similarity.py`), not via agent tool calls. The spec's description of Stage 3 calling find_similar as a tool is obsolete.

### 3. [WRONG] Stage 2 `allowed_tools` (§7 Agent SDK Invocation block)

Spec §7 says `allowed_tools=["Bash"]`. The implementation has `allowed_tools=[]` with comment "empty = all tools permitted, including MCP server tools." These are semantically opposite — spec implies only Bash, code gives full access.

### 4. [WRONG] MCP env key for LangSmith (§11 MCP Configuration Generation)

The spec §11 code example passes the key as `"LANGCHAIN_API_KEY"` to the langsmith MCP server env. The implementation (`setup_mcp.py`) uses `"LANGSMITH_API_KEY"`. The langsmith MCP server is a LangChain ecosystem tool and typically expects `LANGCHAIN_API_KEY`. This is a potential runtime bug in the implementation, but either way there is a spec/code mismatch.

### 5. [WRONG] Exit code table inconsistency (Addendum Gap 8 vs. Gap 4)

Addendum Gap 4 says `run-stage` return code 4 is "Stage-specific failure (e.g. stage 1 git error)." The Gap 8 error table says: "Stage 1 git error | `run-stage` exit 1 | Silent exit | **0**." These directly contradict each other. The implementation exits code 4 for `GitDiffError`. The Gap 8 table is wrong.

### 6. [WRONG] Architecture diagram §4 "NO → stop, no comment"

The diagram says when no behavioral changes are detected: `NO → stop, no comment`. The implementation (and the DECISIONS.md, CHANGELOG, and §6 Gate Logic prose) clearly states a minimal acknowledgment comment IS posted. The diagram is stale.

### 7. [STALE] Phoenix write implementation (§9)

Spec §9 shows `import phoenix as px` / `client = px.Client().upload_dataset(...)`. This is the old API. DECISIONS.md explicitly documents the decision to use `arize-phoenix-client==2.0.0` with `phoenix.client.Client().datasets.*`. The implementation correctly uses `from phoenix.client import Client`. The spec §9 Phoenix write example was never updated.

### 8. [STALE] Spec §10 approval workflow snippet is outdated

Three problems in the §10 workflow snippet:
- Uses `pull_request: types: [closed]` instead of `pull_request_target: types: [closed]` (security concern — `pull_request_target` is the correct trigger to access secrets post-merge)
- Missing the `resolve-run-id` step to find the correct analysis run
- Uses `python -m probegen.write_probes --proposal ProbeProposal.json` instead of `probegen write-probes --proposal .probegen/stage3.json`

The §12 workflow and the generated workflow (`init_cmd.py`) are both correct; §10 is dead example code that was never updated.

---

## Missing Implementations (Documented but not in code)

### 9. [MISSING] Auto-run is entirely unimplemented

Spec §9 extensively documents auto-run: platform-specific eval triggers (LangSmith REST, Braintrust CLI, Phoenix `run_experiment()`, Promptfoo CLI), result collection, and posting a results comment back to the merged PR. The `AutoRunConfig` model exists in `config.py` with `enabled`, `fail_on`, `notify` fields. **None of it is read or executed anywhere.** `write_probes.py` writes probes and posts a write-outcome comment but never triggers an eval run. The entire auto-run pipeline described in §9 is not implemented.

This is the largest gap between documentation and implementation.

### 10. [MISSING] `.probegen/runs/{sha}/` file export is not wired up

Spec §11 documents that every Stage 3 run writes to `.probegen/runs/{commit_sha}/` (BehaviorChangeManifest.json, CoverageGapManifest.json, ProbeProposal.json, probes.yaml, summary.md, metadata.json). The `export.py` module exists with the relevant functions. But `export.py` is only imported in tests — it is not called from `run_stage.py`, `post_comment.py`, or anywhere in the production pipeline. The run artifact directory is never created. The `ProbeProposal.export_formats` fields for `promptfoo` and `deepeval` paths (referenced in the spec's PR comment example) are always null.

### 11. [MISSING] `probegen init` return code 2

Addendum Gap 4 documents return code 2 for "write permission error." The implementation only has `except click.Abort → SystemExit(1)`. No write errors are caught — they'd propagate as unhandled exceptions.

### 12. [MISSING] `embed-batch` return code 2

Addendum Gap 3 says return code 2 is for "cache error (non-fatal — continues without cache)." The implementation prints a stderr warning for cache errors and exits 0, not 2. Code 2 is never emitted.

---

## Spec Internal Issues (Not implementation errors, but spec problems)

### 13. [DX] Spec §14 shows `@dataclass` for all models

Spec §14 shows `EvalCase`, `CoverageSummary`, `ProbeCase` as `@dataclass`. All implementations use Pydantic `BaseModel`. DECISIONS.md documents this choice. The spec §14 gives readers the wrong mental model for validation behavior, JSON serialization, and field validators.

### 14. [DX] Spec §13 embedding config missing `dimensions` field, misleading comment

The spec §13 reference YAML:
```yaml
embedding:
  model: "text-embedding-3-small"   # or: cohere, bge-small-en-v1.5
```
Two issues: the `dimensions` field is implemented and documented in the addendum and DECISIONS.md but omitted from the spec reference. The comment `# or: cohere, bge-small-en-v1.5` is wrong — the embedding implementation calls OpenAI's embeddings API; Cohere and BGE are not supported and would fail at runtime.

### 15. [DX] Spec §13 shows all platforms enabled in reference YAML

Spec §13's full `probegen.yaml` reference shows all four platforms (`langsmith`, `braintrust`, `arize_phoenix`, `promptfoo`) enabled simultaneously. The `probegen.yaml.example` (per the CHANGELOG decision) shows only LangSmith active with others commented out. These are in tension — a user following the spec reference would configure all platforms, which is not the intended setup experience.

### 16. [DX] Spec §12 workflow missing `actions: read` permission

The spec §12 full workflow YAML has:
```yaml
permissions:
  contents: read
  pull-requests: write
```
The generated workflow adds `actions: read`. This is required because `probegen resolve-run-id` queries the GitHub Actions REST API, which needs `actions: read`. Without it, the Stage 4 job fails at the resolve step.

### 17. [DX] Spec §3 Agent SDK invocation example wrong prompt signature

Spec §3 shows: `prompt=render_stage1_prompt(context)`. The actual function signature is `render_stage1_prompt(raw_change_data, context)`. Minor but will confuse a reader trying to understand Stage 1.

### 18. [DX] Spec §5 context pack prompt doesn't match `init` UX

The spec §5 shows an elaborate multi-line prompt with stub file listing that appears when no `context/` directory exists. The actual implementation just does:
```python
click.confirm("5. Create a context/ directory with stub files?", default=True)
```
The elaborate prompt doesn't exist — it is aspirational spec text.

### 19. [DX] The addendum's "Gap 1–8" structure creates confusion

The addendum is titled "Implementation Gaps" and framed as gap-filling notes. It now contains definitive, accurate information (updated per CHANGELOG) but reads like internal scaffolding, not a polished reference. Specifically:
- It contradicts itself in places (Gap 4 says `get-behavior-diff` is called by the agent; the actual architecture doesn't match — see issue #1)
- It has overlapping content with the spec, so readers don't know which is authoritative when they differ
- Gaps 5 and 6 are entirely redundant with what's already in `init_cmd.py` stubs

### 20. [DX] `probegen.yaml.example` bare `dimensions:` key looks like a typo

```yaml
embedding:
  model: "text-embedding-3-small"
  cache_path: ".probegen/embedding_cache.db"
  dimensions:
```
The trailing bare `dimensions:` is valid YAML (null) but looks like an incomplete entry to most readers. Should be `dimensions: null  # optional` or omitted entirely with a comment explaining it.

---

## Smaller DX / Accuracy Issues

### 21. [DX] "bootstrap mode" vs "starter mode" inconsistency within spec

The CHANGELOG says "Bootstrap mode" renamed to "Starter mode" in user-facing PR comment text; internal schema stays `bootstrap`. Spec §1 says "bootstrap mode"; spec §15 uses both "starter mode" and "bootstrap mode" in adjacent paragraphs without clarifying that they're the same thing. Needs a clear one-time explanation: internal value = `bootstrap`, user-facing label = "Starter mode."

### 22. [DX] Setup Step 6 implies hint patterns are behavioral change gates

Spec §13 Step 6 says: "Open a PR that touches a behavior-defining or guardrail artifact — any file matched by your `probegen.yaml` `behavior_artifacts.paths`…" This implies patterns gate what gets analyzed. They don't — the agent analyzes all changed files. A user who modifies `src/config.py::SYSTEM_PROMPT` without a pattern match would still have their change detected. The hint model was a key architectural decision (DECISIONS.md 2026-03-16) but this step description didn't catch up.

### 23. [DX] README Node.js prerequisite underspecified

README says Node.js 22+ is "required in CI… Only needed locally if running `probegen run-stage` directly." Running `probegen run-stage` is exactly what a developer does to test the pipeline locally. The hedge creates false reassurance that Node.js is rarely needed.

### 24. [DX] Spec §3 "CLI (`--print` flag)" reference unexplained

Spec §3 says "The CLI (`--print` flag) is for one-shot scripting." This refers to the Claude Agent SDK CLI's flag, not the probegen CLI. A reader will be confused about what this flag is or where to find it.

### 25. [DX] 2600+ lines of docs with no quick reference; addendum should be merged or deleted

For a tool with a focused scope, this is a large volume of documentation with no quick-reference section. The spec has no "at a glance" summary. A developer who just wants to understand what the schemas look like or what CLI flags exist must scan through architecture rationale and philosophy sections to find it. The addendum in particular should either be integrated into the spec (updating the sections it fills) or deleted — its current form as a parallel document creates divergence over time.

---

## Summary Table

| # | Severity | Location | Issue |
|---|---|---|---|
| 1 | **High** | Addendum Gap 4, §4 | `get-behavior-diff` described as agent-called tool; it's orchestrator-called before agent starts |
| 2 | **High** | §8 | Stage 3 `allowed_tools=["Bash"]` — code is `[]`, ranking done in orchestrator not by agent |
| 3 | **High** | §7 | Stage 2 `allowed_tools=["Bash"]` — code is `[]` (all tools) |
| 4 | **Medium** | §11 | LangSmith MCP env key: spec `LANGCHAIN_API_KEY`, code `LANGSMITH_API_KEY` |
| 5 | **Medium** | Addendum Gap 8 vs Gap 4 | Conflicting exit codes for Stage 1 git error (0 vs 4) |
| 6 | **Medium** | §4 | Architecture diagram says no comment on no-changes; implementation posts one |
| 7 | **Medium** | §9 | Phoenix write example uses old `px.Client()` API |
| 8 | **Medium** | §10 | Approval workflow snippet: wrong trigger, missing steps, wrong command |
| 9 | **High** | §9 | Auto-run entirely unimplemented despite extensive documentation |
| 10 | **High** | §11 | `.probegen/runs/{sha}/` file export not wired into pipeline (`export.py` unused) |
| 11 | Low | Addendum Gap 4 | `init` return code 2 documented but not implemented |
| 12 | Low | Addendum Gap 3 | `embed-batch` return code 2 documented but not emitted |
| 13 | **Medium** | §14 | All models shown as `@dataclass`; all are Pydantic `BaseModel` |
| 14 | **Medium** | §13 | Embedding config: missing `dimensions` field; wrong model comment (cohere/bge not supported) |
| 15 | Low | §13 | All-platforms-enabled reference conflicts with commented-out example |
| 16 | **Medium** | §12 | Workflow missing `actions: read` permission (Stage 4 resolve step fails without it) |
| 17 | Low | §3 | Wrong prompt function signature in Agent SDK invocation example |
| 18 | Low | §5 | Elaborate context pack interactive prompt doesn't match actual `init` UX |
| 19 | **High** | Addendum | Structured as internal scratchpad; contains contradictions; redundant with spec |
| 20 | Low | `probegen.yaml.example` | Bare `dimensions:` key looks like a typo |
| 21 | Low | §1, §15 | "bootstrap mode" vs "starter mode" used interchangeably without explanation |
| 22 | Low | §13 Step 6 | Setup step implies hint patterns gate behavioral change detection |
| 23 | Low | README | Node.js prerequisite hedged in a way that underplays local dev requirement |
| 24 | Low | §3 | SDK `--print` flag reference unexplained; looks like a probegen CLI flag |
| 25 | Low | Overall | No quick-reference section; addendum should be merged into spec or deleted |
