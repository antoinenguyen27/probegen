# Platform Support

Parity is method-first, but the integration surface still matters. The right DX move is to be explicit about support strength.

## Strong

## Promptfoo

- Native path is strongest because assertions live in the config rows.
- Formal evaluator discovery is strong.
- Row-local writeback is straightforward.

Best for teams that keep eval logic in repo-local config.

## LangSmith

- Dataset discovery and writeback are strong.
- Formal evaluator discovery is stronger than other hosted platforms in Parity’s current implementation.
- Parity reuses existing evaluator regimes when it can confirm them.

Best for teams already storing examples in LangSmith datasets.

## Supported With Limitations

## Braintrust

- Dataset writeback works.
- Target discovery is weaker than Promptfoo and LangSmith.
- Evaluator recovery often depends on repo-local scorer or harness assets.

Best when you can give Parity explicit project and dataset hints.

## Arize Phoenix

- Dataset read/write works.
- Evaluator discovery is weaker than Promptfoo and LangSmith because the current client surface exposes less.
- Parity stays conservative and will fall back sooner.

Best when dataset writeback matters more than evaluator topology recovery.

## Built In

## Bootstrap Mode

If Parity cannot find a safe native target, it still produces proposal-oriented starter evals.

Bootstrap mode means:

- useful day-one suggestions
- no fake confidence about existing evaluator infrastructure
- no unsafe automatic writeback

## Out of Scope

Across all platforms, Parity does not:

- run eval suites
- create hosted evaluators
- rebind hosted evaluators
- mutate hosted evaluator infrastructure
