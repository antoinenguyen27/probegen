# Probegen

Probegen detects behaviorally significant pull request changes in LLM systems and proposes targeted evaluation probes for review before writing them to an evaluation platform.

## What it does

Probegen runs in CI on pull requests. It:

1. Detects prompt, instruction, and guardrail changes that are likely to alter agent behavior.
2. Retrieves nearby evaluation coverage from your existing eval stack when mappings exist.
3. Generates ranked probe proposals tailored to the specific change.
4. Exports those probes as files and, after explicit approval, writes them to the configured platform.

Probegen is not an eval runner. It generates eval inputs that plug into LangSmith, Braintrust, Phoenix, Promptfoo, or file-based workflows.

## Prerequisites

- Python 3.11+
- Node.js 22+ for Claude Code / Agent SDK workflows
- An Anthropic API key
- At least one eval platform API key if you want direct platform integration

## Setup

1. Install the package: `pip install probegen`
2. Run interactive setup: `probegen init`
3. Fill in the context pack under [context/product.md](/Users/an/Documents/probeGen/context/product.md), [context/users.md](/Users/an/Documents/probeGen/context/users.md), [context/interactions.md](/Users/an/Documents/probeGen/context/interactions.md), [context/good_examples.md](/Users/an/Documents/probeGen/context/good_examples.md), and [context/bad_examples.md](/Users/an/Documents/probeGen/context/bad_examples.md)
4. Add the required GitHub secrets
5. Copy or commit [.github/workflows/probegen.yml](/Users/an/Documents/probeGen/.github/workflows/probegen.yml) into the target repository

The full configuration reference is available in [probegen.yaml.example](/Users/an/Documents/probeGen/probegen.yaml.example).

## Real example quickstart

If you want to test Probegen against a real LangGraph repo instead of wiring everything from scratch, use the in-repo demo under [examples/langgraph-agentic-rag](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag) and follow [docs/langgraph-agentic-rag-quickstart.md](/Users/an/Documents/probeGen/docs/langgraph-agentic-rag-quickstart.md).

## Context pack and trace safety

Probegen works without a context pack, but probe quality drops significantly. At minimum, fill in product context and known failure modes.

Production traces are never sanitized by the tool. If you add files under [context/traces/README.md](/Users/an/Documents/probeGen/context/traces/README.md), anonymize them first. Remove names, emails, account IDs, and any other sensitive data before committing them.
