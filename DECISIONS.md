# Decisions

## 2026-03-16

### Question
Should config glob patterns act as mandatory filters for which files the Stage 1 agent sees, or as hints that guide its discovery?

### Options considered
1. Keep patterns as pathspec filters passed to `git diff` — agent only sees pre-filtered files.
2. Pass all changed files to the agent; use patterns only to decide which files to pre-load with full content.

### Choice
Patterns as hints (option 2).

### Reasoning
The spec explicitly states Stage 1 uses the Agent SDK because it "encounters the actual codebase and reasons about what it finds." Pattern-as-filter contradicts this: a team storing their system prompt as `src/config.py::SYSTEM_PROMPT = "..."` with no matching glob silently gets zero behavioral analysis. The hint model gives the agent full visibility while preserving the efficiency benefit of pre-loading likely-relevant files. Config patterns are now surfaced to the agent as guidance alongside the full changed-file list.

---

### Question
Should the PR comment be silent (no post) when Stage 1 detects no behavioral changes, or should it post a minimal acknowledgment?

### Options considered
1. Post nothing — most noise-free path for PRs that don't touch behavioral artifacts.
2. Post a minimal "no behavioral changes detected" comment — gives developers confirmation and a route to investigate if Parity missed something.

### Choice
Post a minimal no-changes comment.

### Reasoning
Silent non-posts leave developers unsure whether Parity ran at all, whether it was misconfigured, or whether it genuinely found nothing. The minimal comment confirms the tool ran successfully and provides a concrete pointer (`behavior_artifacts` hint patterns) if the developer suspects a false negative. The comment is short and informational, not a workflow blocker.

---

## 2026-03-14

### Question
Should the schema contracts be implemented as standard-library dataclasses or as Pydantic models?

### Options considered
1. Standard-library dataclasses plus separate validators and JSON schema adapters.
2. Pydantic `BaseModel` contracts with strict validation and JSON-schema support.

### Choice
Pydantic `BaseModel` contracts.

### Reasoning
Parity's stage boundaries, CLI commands, and artifact exports are all JSON-mediated. Strict runtime validation, schema generation, and consistent `model_validate` / `model_dump` behavior are core requirements. `BaseModel` provides those directly and keeps the contract layer smaller and less error-prone than parallel dataclass + validator implementations.

### Question
Which Phoenix client dependency should Parity target?

### Options considered
1. `arize-phoenix` with the older `px.Client().upload_dataset(...)` path from the spec examples.
2. The current `arize-phoenix-client` package with `phoenix.client.Client().datasets.*`.

### Choice
`arize-phoenix-client==2.0.0`.

### Reasoning
The current Phoenix documentation and live package expose dataset read/write operations through `phoenix.client.Client().datasets`. The spec's older `px.Client()` example is stale. Implementing against the current client reduces integration risk; the resulting behavior is still aligned with the spec's intended Phoenix read/write support.

### Question
How should the repository support `python -m parity.write_probes` when the requested tree places the main implementation under `parity/cli/write_probes.py`?

### Options considered
1. Ignore the `python -m parity.write_probes` entrypoint and require the CLI command only.
2. Add a thin top-level `parity/write_probes.py` compatibility wrapper that delegates to the CLI implementation.

### Choice
Add the wrapper module.

### Reasoning
The workflow specification invokes `python -m parity.write_probes`. Supporting that invocation avoids a broken Stage 4 path without changing the primary CLI implementation layout.

### Question
How should optional embedding dimensionality be represented in configuration when the main config reference omits it but the addendum mentions it?

### Options considered
1. Omit the setting and always use the model default dimensions.
2. Add an optional `embedding.dimensions` field while defaulting to the model default.

### Choice
Add optional `embedding.dimensions`.

### Reasoning
The addendum explicitly references configured dimension reduction for `text-embedding-3-*` models. Making the field optional preserves backward compatibility with the main spec example while supporting the documented API capability.

### Question
How should Parity handle the current Claude Agent SDK `error_max_turns` subtype, which is documented in the live SDK but not in the original spec?

### Options considered
1. Ignore `error_max_turns` and treat it as a generic stage error.
2. Handle `error_max_turns` the same way as other stage limits, alongside `error_max_budget_usd`.

### Choice
Handle `error_max_turns` as a limit-exceeded condition.

### Reasoning
The current SDK documents `error_max_turns` as a distinct result subtype. Treating it like the existing budget-limit path keeps stage failure handling coherent and prevents a newer SDK behavior from bypassing the retry/partial-result machinery.

### Question
How should `run-stage` behave when `parity.yaml` is missing, given the spec's graceful-degradation principle conflicts with the standalone `get-behavior-diff` contract?

### Options considered
1. Fail `run-stage` immediately whenever the config file is missing.
2. Let standalone `get-behavior-diff` stay strict, but allow `run-stage` to fall back to default config values.

### Choice
Standalone `get-behavior-diff` remains strict; `run-stage` falls back to defaults.

### Reasoning
This preserves the exact CLI contract for the deterministic tool while honoring the higher-level non-blocking/graceful-degradation principle for the main pipeline entrypoint.

### Question
How should Stage 4 choose probes when the proposal schema includes `approved`, but v1 approval is label-based and the example proposal leaves every probe as `approved: false`?

### Options considered
1. Write only probes with `approved: true`, which would usually write nothing.
2. If any probes are explicitly marked approved, write only those; otherwise treat label approval as approval for the whole proposal.

### Choice
Write only explicitly approved probes when they exist; otherwise write the full proposal set.

### Reasoning
That preserves compatibility with a future per-probe approval workflow without breaking the v1 label-based "approve the whole proposal" behavior described in the spec.

### Question
How should Parity behave when no eval corpus exists yet for a changed artifact?

### Options considered
1. Treat missing or empty eval coverage as a degraded error path and only warn.
2. Treat missing or empty eval coverage as a first-class bootstrap mode that still generates starter probes from the diff and product context.

### Choice
Treat it as a first-class bootstrap mode.

### Reasoning
Parity's adoption path depends on being useful before a team has mature eval hygiene. Bootstrap mode preserves day-one usefulness while still being honest that corpus-based novelty and boundary analysis improve once baseline evals exist. This matches the tool's non-blocking, review-aid positioning and the broader "works out of the box, gets better with more context" product story.
