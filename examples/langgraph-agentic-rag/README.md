# LangGraph Agentic RAG Example

This example is a deliberately small repository that makes Probegen's PR-to-probe workflow easy to test with a real LangGraph app.

It is adapted from LangGraph's agentic RAG pattern, but the emphasis here is not on app complexity. The emphasis is on making behavioral changes easy to observe, easy to map to an eval dataset, and easy for Probegen to turn into targeted probes.

This demo is deliberately coverage-aware: it seeds a small LangSmith dataset so Stage 2 has something real to compare against. Probegen itself can also run without that baseline, in which case it falls back to bootstrap probe generation.

## What this demo is built to show

1. A baseline LangGraph RAG agent that answers from a small knowledge base.
2. A seeded LangSmith dataset with partial coverage.
3. A prompt-only change that should create obvious evaluation gaps.
4. The full Probegen workflow:
   - detect the behavior change,
   - compare it to existing eval coverage,
   - propose new probes in the PR,
   - write approved probes back to LangSmith after merge.

## Repository layout

- `app/`: the LangGraph app
- `knowledge_base/`: local markdown files used for retrieval
- `prompts/`: behavior-defining prompts that Probegen watches
- `judges/`: guardrail/judge instructions that Probegen watches
- `context/`: Probegen context pack
- `.github/workflows/probegen.yml`: GitHub Actions workflow for the copied demo repo
- `scripts/seed_langsmith_dataset.py`: seeds the baseline eval dataset
- `changes/always_cite.patch`: intentional PR change used in the tutorial
- `expected_outputs/`: illustrative Stage 1-3 artifacts for the patch

## Fast path

1. Copy this directory into its own Git repository.
2. Follow [docs/langgraph-agentic-rag-quickstart.md](/Users/an/Documents/probeGen/docs/langgraph-agentic-rag-quickstart.md).
3. Seed the LangSmith dataset.
4. Open a PR using `changes/always_cite.patch`.
5. Review the generated Probegen comment and artifacts.

## Local app smoke test

Install the app dependencies, then run:

```bash
python -m app.main "How long are exports available?"
python -m app.main "Thanks for the help"
```

The first question should retrieve and cite a knowledge-base document. The second should answer naturally without citations in the baseline branch.
