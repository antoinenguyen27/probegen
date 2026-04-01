# Architecture

This is the maintained high-level architecture for Parity.

## Product Contract

Parity:

1. inspects the behavioral change introduced by a PR
2. discovers the most relevant existing eval target and method
3. validates whether the changed behavior is already covered
4. synthesizes target-faithful eval additions
5. writes only safe native additions after explicit approval

Parity does not:

- run eval suites
- create or mutate hosted evaluator infrastructure
- force all targets through one generic row schema

## Runtime Flow

```text
PR diff
  -> Stage 1: BehaviorChangeManifest
  -> Stage 2: EvalAnalysisManifest
  -> Stage 3: EvalProposalManifest
  -> write-evals: native_ready renderings only
```

## Stage 1: Behavior Change Analysis

Stage 1 is repo-focused and security-constrained.

It:

- looks at the full PR file list
- uses configured hints to pre-load likely behavior-defining files
- determines which changes are behaviorally meaningful
- produces retrieval-friendly evidence for downstream stages

Main output: `BehaviorChangeManifest`

## Stage 2: Eval Analysis

Stage 2 is the discovery and validation stage.

It:

- resolves the best matching eval target
- preserves native sample shape
- discovers evaluator regime where possible
- validates whether coverage gaps are real
- falls back to bootstrap mode when no safe native target is available

Main output: `EvalAnalysisManifest`

## Stage 3: Native Eval Synthesis

Stage 3 is the constructive stage.

It:

- reads Stage 2 evidence directly
- generates candidate eval intents
- relies on host reranking and diversity limits
- produces native renderings and evaluator plans for the final proposal

Main output: `EvalProposalManifest`

## Deterministic Writeback

`parity write-evals` is deterministic and host-owned.

It:

- groups renderings by target
- writes only `native_ready` renderings
- skips `review_only`
- reports unsupported or failed targets

It does not run evals or mutate evaluator infrastructure.

## Design Principles

Parity should preserve:

- method-first target understanding
- evidence-rich handoffs between stages
- deterministic writeback
- minimal surprise in the public surface
- compatibility with the user’s existing eval system
