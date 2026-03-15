# Probegen

Probegen detects behaviorally significant pull request changes in LLM systems and proposes targeted evaluation probes for review before writing them to an evaluation platform.

## What it does

Probegen runs in CI on pull requests. It:

1. Detects changes to prompts, instructions, guardrails, validators, tool descriptions, classifiers, retry policies, output schemas, and other agent harness artifacts that are likely to alter agent behavior.
2. Retrieves nearby evaluation coverage from your existing eval stack when mappings exist.
3. Falls back to bootstrap probe generation when no eval corpus exists yet.
4. Generates ranked probe proposals tailored to the specific change, including multi-turn conversational probes when the agent is conversational.
5. Exports those probes as files and, after explicit approval, writes them to the configured platform.

Probegen is not an eval runner. It generates eval inputs that plug into LangSmith, Braintrust, Arize Phoenix, Promptfoo, or file-based workflows.

Probegen works out of the box even if you have no evals yet. In that case it bootstraps plausible starter probes from the diff, system prompt or guardrails, and whatever product context you provide. The more eval coverage and product detail you give it, the sharper its novelty detection and boundary analysis become.

## Prerequisites

- Python 3.11+
- Node.js 22+ for Claude Code / Agent SDK workflows
- An Anthropic API key
- An eval platform API key only if you want direct platform integration or automatic writeback

## Setup

1. Install the package: `pip install probegen`
2. Run interactive setup: `probegen init`
3. Fill in the context pack under [context/product.md](context/product.md), [context/users.md](context/users.md), [context/interactions.md](context/interactions.md), [context/good_examples.md](context/good_examples.md), and [context/bad_examples.md](context/bad_examples.md)
4. Add the required GitHub secrets
5. Copy or commit [.github/workflows/probegen.yml](.github/workflows/probegen.yml) into the target repository

The full configuration reference is available in [probegen.yaml.example](probegen.yaml.example).

## Real example quickstart

If you want to test Probegen against a real LangGraph repo instead of wiring everything from scratch, use the in-repo demo under [examples/langgraph-agentic-rag](examples/langgraph-agentic-rag) and follow [examples/langgraph-agentic-rag/docs/quickstart.md](examples/langgraph-agentic-rag/docs/quickstart.md).

## Context pack and trace safety

Probegen works without a context pack, but probe quality drops significantly. At minimum, fill in product context and known failure modes. This matters even more in bootstrap mode, where Probegen has no existing eval corpus to compare against.

Production traces are never sanitized by the tool. If you add files under `context/traces/`, anonymize them first. Remove names, emails, account IDs, and any other sensitive data before committing them.
