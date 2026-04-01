# Decisions

These are the current durable product decisions that shape the public surface.

## Method-First, Not Platform-First

Parity optimizes for discovering how the target eval system works before deciding where new evals should land.

Implication:

- target method and evaluator regime matter more than platform branding

## Discovery and Reuse Only

Parity discovers evaluator regimes and reuses them when it can confirm them safely.

Implication:

- Parity does not create, rebind, or mutate hosted evaluator infrastructure

## Deterministic Writeback

Writeback stays outside the agent stages and writes only `native_ready` renderings.

Implication:

- proposal generation and writeback are intentionally separate safety boundaries

## Narrow Public Surface

Parity exposes only config that materially affects analysis or synthesis behavior.

Implication:

- the approval label is fixed to `parity:approve`
- workflow policy belongs in `.github/workflows/parity.yml`
