# Parity

[![PyPI](https://img.shields.io/pypi/v/parity-ai)](https://pypi.org/project/parity-ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

You changed a prompt. You don't know if you broke something.

Parity automatically generates evals for every AI change — on every pull request, before anything ships.

## What it does

Parity runs in CI. For every PR that touches your AI system, it:

1. Detects changes to prompts, instructions, guardrails, validators, tool descriptions, classifiers, and other artifacts that shape how your agent behaves.
2. Checks your existing eval coverage against what changed — and finds the gaps.
3. Generates ranked, targeted test cases for those gaps, including multi-turn conversational evals for conversational agents.
4. Posts a PR comment with proposed evals for your review.
5. Writes approved evals to your eval platform only after you explicitly label the PR.

Parity is not an eval runner. It generates eval inputs that plug into LangSmith, Braintrust, Arize Phoenix, Promptfoo, or file-based workflows.

No evals yet? Parity starts from zero. It generates starter evals from your diff, system prompt, and whatever product context you provide. The more context you give it, the sharper it gets.

## Quick start

```bash
pip install parity-ai
parity init
```

`parity init` generates `parity.yaml`, a GitHub Actions workflow, and `context/` stubs. Fill in your context files, add your API keys as GitHub secrets, and open a PR that touches a prompt.

See [docs/configuration.md](docs/configuration.md) for prerequisites, spend caps, proposal-count controls, and the full configuration reference.

## Try it on a real example

Test Parity against a real LangGraph repo with the in-repo demo:

[examples/langgraph-agentic-rag](examples/langgraph-agentic-rag) | [quickstart guide](examples/langgraph-agentic-rag/docs/quickstart.md)

## License

[MIT](LICENSE)
