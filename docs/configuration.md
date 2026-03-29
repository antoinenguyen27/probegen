# Configuration

## Prerequisites

- Python 3.11+
- Node.js 22+ — required everywhere (CI and local). Parity runs Agent SDK sessions via Node.js. Even running `parity run-stage` locally requires Node.js installed and available in your PATH.
- An Anthropic API key
- An eval platform API key — only needed for direct platform integration or automatic writeback

## Spend caps

Parity does not require any spend configuration for normal usage. If you omit `spend:` in `parity.yaml`, it falls back to an internal default total analysis cap.

For standard users, the only spend knob that matters is:

- `spend.analysis_total_spend_cap_usd`

Parity allocates that total across:

- Stage 1 agent spend
- Stage 2 agent spend
- Stage 2 embedding spend
- Stage 3 agent spend

Advanced users can override the four stage-specific caps directly, but they are expert-only controls.

Important scope note:

- `analysis_total_spend_cap_usd` is a true Parity analysis spend cap, not just an Agent SDK cap.
- Stage 2 embedding spend is tracked separately from Stage 2 agent spend.
- Stage 3 also has an internal context-packing token limit, but that is not a normal user-facing configuration knob.

Typical overall spend for a single PR run is still usually modest, but exact spend depends on diff size, mapping quality, eval corpus size, and how much retrieval or generation work each stage performs.

## Advanced configuration

The full configuration reference is available in [parity.yaml.example](../parity.yaml.example).

The main reviewer-facing generation knob is:

- `generation.proposal_probe_limit`

Parity also supports:

- `generation.candidate_probe_pool_limit`

That controls internal search breadth before reranking and diversity filtering. Most users should leave it alone and tune only the final proposal size.

## Context files

Parity works without context files, but eval quality drops significantly. At minimum, fill in product context and known failure modes. This matters even more in bootstrap mode, where Parity has no existing eval corpus to compare against.

Run `parity init` to generate context stubs, then fill in:

- `context/product.md` — what the product does, who uses it, the agent's role
- `context/bad_examples.md` — known failure modes and edge cases
- `context/good_examples.md` — what good responses look like
- `context/users.md` — user profiles and how they phrase requests
- `context/interactions.md` — common conversation flows

## Trace safety

Production traces are never sanitized by the tool. If you add files under `context/traces/`, anonymize them first — remove names, emails, account IDs, and any other sensitive data before committing.
