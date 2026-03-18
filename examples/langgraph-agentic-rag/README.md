# LangGraph Agentic RAG Example

This example is a deliberately small repository that makes Probegen's PR-to-probe workflow easy to test with a real LangGraph app.

It implements the LangGraph agentic RAG reference pattern directly — retrieval from Lilian Weng's ML research blog posts, document grading, question rewriting, and answer generation — with all prompts defined as inline Python variables. The emphasis is on making behavioral changes easy to observe, easy to map to an eval dataset, and easy for Probegen to turn into targeted probes.

This demo is deliberately coverage-aware: it seeds a small LangSmith dataset so Stage 2 has something real to compare against. Probegen itself can also run without that baseline, in which case it falls back to bootstrap probe generation.

## What this demo is built to show

1. A baseline LangGraph RAG agent that retrieves from three Lilian Weng blog posts.
2. A seeded LangSmith dataset with partial coverage.
3. A single prompt change (`GENERATE_PROMPT`) that adds a citation requirement — an individually reasonable addition that introduces a non-obvious risk because the retriever does not expose source metadata to the generator.
4. The full Probegen workflow:
   - detect the behavior change,
   - compare it to existing eval coverage,
   - propose new probes in the PR,
   - write approved probes back to LangSmith after merge.

## Repository layout

- `app/`: the LangGraph app — all prompts are inline Python variables in `app/graph.py`
- `context/`: Probegen context pack
- `.github/workflows/probegen.yml`: GitHub Actions workflow for the copied demo repo
- `scripts/seed_langsmith_dataset.py`: seeds the baseline eval dataset in LangSmith
- `changes/always_cite.patch`: intentional PR change used in the tutorial (modifies `GENERATE_PROMPT` in `app/graph.py`)
- `expected_outputs/`: illustrative Stage 1-3 artifacts for the patch

## Getting started

Follow [docs/quickstart.md](docs/quickstart.md). It covers environment setup, seeding the LangSmith dataset, applying the demo patch, and inspecting Probegen's output end-to-end.

## Knowledge base

Documents are loaded at startup from three Lilian Weng blog posts via `WebBaseLoader`:

- https://lilianweng.github.io/posts/2024-11-28-reward-hacking/
- https://lilianweng.github.io/posts/2024-07-07-hallucination/
- https://lilianweng.github.io/posts/2024-04-12-diffusion-video/
