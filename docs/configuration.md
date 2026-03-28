# Configuration

## Prerequisites

- Python 3.11+
- Node.js 22+ — required everywhere (CI and local). Parity runs Agent SDK sessions via Node.js. Even running `parity run-stage` locally requires Node.js installed and available in your PATH.
- An Anthropic API key
- An eval platform API key — only needed for direct platform integration or automatic writeback

## Cost control

Each stage has a configurable Anthropic API spend budget (see `budgets:` in `parity.yaml`). Typical costs per PR:

- Stage 1 (change detection): $0.05–0.30
- Stage 2 (coverage analysis): $0.10–0.50
- Stage 3 (eval generation): $0.10–0.60

Increase budget limits if stages time out on large diffs or complex repos.

## Advanced configuration

The full configuration reference is available in [parity.yaml.example](../parity.yaml.example).

## Context files

Parity works without context files, but eval quality drops significantly. At minimum, fill in product context and known failure modes. This matters even more in starter mode, where Parity has no existing eval corpus to compare against.

Run `parity init` to generate context stubs, then fill in:

- `context/product.md` — what the product does, who uses it, the agent's role
- `context/bad_examples.md` — known failure modes and edge cases
- `context/good_examples.md` — what good responses look like
- `context/users.md` — user profiles and how they phrase requests
- `context/interactions.md` — common conversation flows

## Trace safety

Production traces are never sanitized by the tool. If you add files under `context/traces/`, anonymize them first — remove names, emails, account IDs, and any other sensitive data before committing.
