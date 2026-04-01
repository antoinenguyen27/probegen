# Configuration

`parity.yaml` is intentionally narrow. It should control how Parity discovers behavior changes, where it is allowed to look for eval targets, how strict writeback should be, and how much analysis budget it can spend.

If a setting does not reliably change product behavior, it should not be in this file.

## Prerequisites

- Python 3.11+
- Node.js 22+ in GitHub Actions
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY` for embedding-backed similarity checks
- Platform API keys only for the platforms you use

## Real Config Surface

## `behavior_artifacts` and `guardrail_artifacts`

These are Stage 1 discovery hints.

- `paths`
- `python_patterns`
- `exclude`

They are hints, not hard filters. Stage 1 still sees the full PR file list and can inspect other files on demand.

## `context`

Controls where Parity reads product context:

- `product`
- `users`
- `interactions`
- `good_examples`
- `bad_examples`
- `traces_dir`
- `trace_max_samples`

Parity works without a complete context pack, but quality drops quickly.

## `platforms`

Declares the integrations available in this repo:

- `langsmith`
- `braintrust`
- `arize_phoenix`
- `promptfoo`

Platform config only declares credentials and target defaults. It does not hardcode synthesis decisions.

## `evals.discovery`

Controls where Stage 2 is allowed to look:

- `repo_asset_globs`
- `platform_discovery_order`
- `sample_limit_per_target`
- `allow_repo_asset_discovery`

Use discovery settings to shape search, not to force a write target.

## `evals.rules`

Rules let you express strong hints for a changed artifact:

- `artifact`
- `preferred_platform`
- `preferred_target`
- `preferred_project`
- `allowed_methods`
- `preferred_methods`
- `repo_asset_hints`

Rules are hints and constraints. If a preferred target is stale or unrelated, Stage 2 can recover to a better target on the same platform.

## `evals.write`

These settings gate deterministic writeback:

- `require_native_rendering`
- `min_render_confidence`
- `create_missing_targets`
- `allow_review_only_exports`

`parity write-evals` only writes `native_ready` renderings.

## `evals.evaluators`

These settings control how strict Parity is when reusing an existing evaluator regime:

- `formal_discovery_required`
- `allow_inference_fallback`
- `require_binding_verification`
- `min_binding_confidence`

Parity does not create, rebind, or mutate hosted evaluators.

## `embedding` and `similarity`

These settings affect Stage 2 novelty and nearest-case comparison:

- `embedding.model`
- `embedding.cache_path`
- `embedding.dimensions`
- `similarity.duplicate_threshold`
- `similarity.boundary_threshold`

They affect evidence gathering, not writeback policy.

## `generation`

These settings bound Stage 3 output size:

- `proposal_limit`
- `candidate_intent_pool_limit`
- `diversity_limit_per_gap`

Stage 3 generates a candidate pool first. The host reranks and caps the final proposal list.

## `spend`

Controls analysis budget:

- `analysis_total_spend_cap_usd`
- `stage1_agent_cap_usd`
- `stage2_agent_cap_usd`
- `stage2_embedding_cap_usd`
- `stage3_agent_cap_usd`
- `budget_policy`

Most users should set only `analysis_total_spend_cap_usd`.

## Workflow Contract

The supported GitHub workflow contract is intentionally fixed:

- The approval label is always `parity:approve`
- Generated workflow policy lives in `.github/workflows/parity.yml`
- If you want a different merge/write policy, edit the workflow directly

Legacy `approval` and `auto_run` sections are still accepted for backward compatibility, but they are deprecated and ignored by the supported scaffold/runtime path.

## Main Commands

- `parity run-stage 1`
- `parity run-stage 2 --manifest`
- `parity run-stage 3 --manifest --analysis`
- `parity write-evals --proposal`

## Reference

See [parity.yaml.example](../parity.yaml.example) for the maintained example configuration.
