# Decisions

## 2026-03-14

### Question
Should the schema contracts be implemented as standard-library dataclasses or as Pydantic models?

### Options considered
1. Standard-library dataclasses plus separate validators and JSON schema adapters.
2. Pydantic `BaseModel` contracts with strict validation and JSON-schema support.

### Choice
Pydantic `BaseModel` contracts.

### Reasoning
Probegen's stage boundaries, CLI commands, and artifact exports are all JSON-mediated. Strict runtime validation, schema generation, and consistent `model_validate` / `model_dump` behavior are core requirements. `BaseModel` provides those directly and keeps the contract layer smaller and less error-prone than parallel dataclass + validator implementations.

### Question
Which Phoenix client dependency should Probegen target?

### Options considered
1. `arize-phoenix` with the older `px.Client().upload_dataset(...)` path from the spec examples.
2. The current `arize-phoenix-client` package with `phoenix.client.Client().datasets.*`.

### Choice
`arize-phoenix-client==2.0.0`.

### Reasoning
The current Phoenix documentation and live package expose dataset read/write operations through `phoenix.client.Client().datasets`. The spec's older `px.Client()` example is stale. Implementing against the current client reduces integration risk; the resulting behavior is still aligned with the spec's intended Phoenix read/write support.

### Question
How should the repository support `python -m probegen.write_probes` when the requested tree places the main implementation under `probegen/cli/write_probes.py`?

### Options considered
1. Ignore the `python -m probegen.write_probes` entrypoint and require the CLI command only.
2. Add a thin top-level `probegen/write_probes.py` compatibility wrapper that delegates to the CLI implementation.

### Choice
Add the wrapper module.

### Reasoning
The workflow specification invokes `python -m probegen.write_probes`. Supporting that invocation avoids a broken Stage 4 path without changing the primary CLI implementation layout.

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
How should Probegen handle the current Claude Agent SDK `error_max_turns` subtype, which is documented in the live SDK but not in the original spec?

### Options considered
1. Ignore `error_max_turns` and treat it as a generic stage error.
2. Handle `error_max_turns` the same way as other stage limits, alongside `error_max_budget_usd`.

### Choice
Handle `error_max_turns` as a limit-exceeded condition.

### Reasoning
The current SDK documents `error_max_turns` as a distinct result subtype. Treating it like the existing budget-limit path keeps stage failure handling coherent and prevents a newer SDK behavior from bypassing the retry/partial-result machinery.

### Question
How should `run-stage` behave when `probegen.yaml` is missing, given the spec's graceful-degradation principle conflicts with the standalone `get-behavior-diff` contract?

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
