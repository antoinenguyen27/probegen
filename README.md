# Parity

[![PyPI](https://img.shields.io/pypi/v/parity-ai)](https://pypi.org/project/parity-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

Parity analyzes behavior-defining AI changes in pull requests, discovers the most relevant existing eval target, validates the real coverage gaps, and proposes native eval additions that fit the target suite.

Parity is not an eval runner. It does not create or mutate hosted evaluator infrastructure. It reuses the eval system you already have.

## What Parity Does

For each PR that changes prompts, instructions, guardrails, judges, validators, or similar behavior-defining assets, Parity:

1. Detects the behavioral change.
2. Resolves the best matching eval target and method.
3. Validates which gaps are actually uncovered.
4. Synthesizes native eval additions for that target.
5. Writes only `native_ready` evals after explicit approval.

## Support

| Path | Status | Notes |
|---|---|---|
| Promptfoo | Strong | Best fully native path. Assertions are row-local and writeback is straightforward. |
| LangSmith | Strong | Strong dataset discovery and writeback. Evaluator reuse is supported; evaluator mutation is out of scope. |
| Braintrust | Supported with limitations | Writeback works. Target discovery is weaker and evaluator recovery depends more on repo assets. |
| Arize Phoenix | Supported with limitations | Dataset read/write works. Evaluator discovery is weaker than Promptfoo and LangSmith. |
| Bootstrap mode | Built in | If no safe target is found, Parity proposes starter evals and abstains from unsafe writeback. |

More detail: [docs/platforms.md](docs/platforms.md)

## Public Commands

These are the commands most users need:

- `parity init`
- `parity doctor`
- `parity run-stage 1`
- `parity run-stage 2`
- `parity run-stage 3`
- `parity write-evals`

Parity also ships lower-level operational commands for GitHub comments, run lookup, embeddings, and similarity, but those are advanced surfaces rather than the main product path.

## Quick Start

```bash
pip install parity-ai
parity init
```

Then:

1. Fill in the generated `context/` files.
2. Add GitHub secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and any platform keys you use.
3. Commit `parity.yaml`, `.github/workflows/parity.yml`, and `context/`.
4. Open a PR that changes AI behavior.
5. Add the fixed approval label `parity:approve` before merging if you want Parity to write approved evals back after merge.

## Docs

- [Configuration](docs/configuration.md)
- [Architecture](docs/spec.md)
- [Platform support](docs/platforms.md)
- [Example quickstart](examples/langgraph-agentic-rag/docs/quickstart.md)
- [Maintainer guide](docs/maintainers.md)

## License

[MIT](LICENSE)
