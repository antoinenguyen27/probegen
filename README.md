# Parity

Parity detects behaviorally significant pull request changes in LLM systems and proposes targeted evaluation probes for review before writing them to an evaluation platform. Parity is **non-blocking** — it runs as a parallel CI job and never prevents PR merges.

## What it does

Parity runs in CI on pull requests. It:

1. Detects changes to prompts, instructions, guardrails, validators, tool descriptions, classifiers, retry policies, output schemas, and other agent harness artifacts that are likely to alter agent behavior.
2. Retrieves nearby evaluation coverage from your existing eval stack when mappings exist.
3. Falls back to starter probe generation when no eval corpus exists yet.
4. Generates ranked probe proposals tailored to the specific change, including multi-turn conversational probes when the agent is conversational.
5. Exports those probes as files and, after explicit approval, writes them to the configured platform.

Parity is not an eval runner. It generates eval inputs that plug into LangSmith, Braintrust, Arize Phoenix, Promptfoo, or file-based workflows.

Parity works out of the box even if you have no evals yet. In that case it generates plausible starter probes from the diff, system prompt or guardrails, and whatever product context you provide. The more eval coverage and product detail you give it, the sharper its novelty detection and boundary analysis become.

## Prerequisites

- Python 3.11+
- Node.js 22+ — required everywhere (CI and local). Parity runs Agent SDK sessions via Node.js. Even running `Parity run-stage` locally requires Node.js to be installed and available in your PATH.
- An Anthropic API key
- An eval platform API key only if you want direct platform integration or automatic writeback

## Quick Start (GitHub Action)

1. Install the package: `pip install Parity`
2. Run interactive setup: `Parity init` — generates `Parity.yaml`, workflow file, and `context/` stubs
3. Fill in `context/product.md` and `context/bad_examples.md` (and other context files for best results)
4. Add GitHub secrets:

   | Secret | Purpose | Where to get it |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | Required — powers all three stages | console.anthropic.com → API Keys |
   | `OPENAI_API_KEY` | Required for coverage-aware mode | platform.openai.com → API Keys |
   | `LANGSMITH_API_KEY` | If using LangSmith | smith.langchain.com → Settings |
   | `BRAINTRUST_API_KEY` | If using Braintrust | braintrust.dev → Settings |
   | `PHOENIX_API_KEY` | If using Arize Phoenix | app.phoenix.arize.com → Settings |

5. Create the approval label in GitHub:
   ```
   gh label create "Parity:approve" --color 0075ca --description "Approve Parity probe writeback"
   ```
6. Commit `Parity.yaml`, `.github/workflows/Parity.yml`, and `context/`.
7. Open a PR that touches a prompt or guardrail.
8. Run `Parity doctor` to verify your setup.

## Cost control

Each stage has a configurable Anthropic API spend budget (see `budgets:` in `Parity.yaml`). Typical costs per PR:

- Stage 1 (change detection): $0.05–0.30
- Stage 2 (coverage analysis): $0.10–0.50
- Stage 3 (probe generation): $0.10–0.60

Increase budget limits if stages time out on large diffs or complex repos.

## Advanced Configuration

The full configuration reference is available in [Parity.yaml.example](Parity.yaml.example).

## Real example quickstart

If you want to test Parity against a real LangGraph repo instead of wiring everything from scratch, use the in-repo demo under [examples/langgraph-agentic-rag](examples/langgraph-agentic-rag) and follow [examples/langgraph-agentic-rag/docs/quickstart.md](examples/langgraph-agentic-rag/docs/quickstart.md).

## Context pack and trace safety

Parity works without a context pack, but probe quality drops significantly. At minimum, fill in product context and known failure modes. This matters even more in starter mode, where Parity has no existing eval corpus to compare against.

Production traces are never sanitized by the tool. If you add files under `context/traces/`, anonymize them first. Remove names, emails, account IDs, and any other sensitive data before committing them.
