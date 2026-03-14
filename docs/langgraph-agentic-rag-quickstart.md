# Quickstart: Real Probegen Test With A LangGraph Agentic RAG Demo

This quickstart gives you a real end-to-end Probegen exercise:

1. create a throwaway repo from a working LangGraph example
2. connect it to LangSmith
3. add the Probegen GitHub Action
4. open a PR that changes agent behavior
5. inspect the generated gaps and probes
6. approve and merge the PR
7. confirm that the approved probes are written back to LangSmith

The demo lives in [examples/langgraph-agentic-rag](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag). It is based on LangGraph's agentic RAG pattern, but simplified so the evaluation story is the main event.

This quickstart intentionally uses a seeded LangSmith dataset so you can observe Probegen's coverage-aware mode. Probegen does not require an existing eval corpus to run; without one, it falls back to bootstrap mode and proposes starter evals from the diff and context pack.

## Why this example

This repo uses agentic RAG instead of the SQL example because it gives Probegen more useful evaluation surfaces without adding setup drag:

- retrieval vs conversational routing
- question rewriting
- relevance grading
- grounded answering
- unsupported-question handling
- citation boundaries

That combination makes it easier to generate multiple high-signal probes from a small prompt change.

## What you should create

Create a new GitHub repo from the example directory:

```bash
cp -R examples/langgraph-agentic-rag /tmp/acme-rag-probegen-demo
cd /tmp/acme-rag-probegen-demo
git init
git add .
git commit -m "Initial LangGraph RAG demo"
gh repo create acme-rag-probegen-demo --private --source=. --push
```

If you prefer, you can copy the directory manually instead of using `cp`.

## Prerequisites

- Python 3.11+
- Node.js 22+
- a GitHub repo where you can add Actions secrets
- `OPENAI_API_KEY` for the LangGraph app
- `ANTHROPIC_API_KEY` for Probegen's generation stages
- `LANGSMITH_API_KEY` for the eval dataset and merge-time writeback

## Step 1: Install dependencies and smoke test the app

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Populate the environment variables in `.env`, then run:

```bash
python -m app.main "How long are exports available after I create one?"
python -m app.main "Thanks for the help"
```

Baseline behavior should look like this:

- factual product questions retrieve a knowledge-base document and cite it
- casual conversational replies stay conversational and do not force citations

## Step 2: Seed the baseline eval dataset in LangSmith

The example expects a LangSmith dataset named `acme-rag-baseline`, which is already wired in [examples/langgraph-agentic-rag/probegen.yaml](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/probegen.yaml).

Run:

```bash
python scripts/seed_langsmith_dataset.py
```

This creates a small baseline eval set with factual retrieval coverage and one unsupported-question case, but it intentionally leaves some citation-boundary gaps.

## Step 3: Configure GitHub secrets

Add these repository secrets:

- `ANTHROPIC_API_KEY`
- `LANGSMITH_API_KEY`

You do not need `OPENAI_API_KEY` for the Probegen CI workflow because Probegen does not run the app itself.

## Step 4: Review the Probegen config and workflow

This example already includes:

- [examples/langgraph-agentic-rag/probegen.yaml](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/probegen.yaml)
- [examples/langgraph-agentic-rag/.github/workflows/probegen.yml](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/.github/workflows/probegen.yml)
- a filled-out context pack under [examples/langgraph-agentic-rag/context](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/context)

The important detail is the mapping from `prompts/answer.md` to the LangSmith dataset. That gives Stage 2 something real to compare against in coverage-aware mode. If you skip this step in a real repo, Probegen still works, but it will generate bootstrap probes without corpus comparisons.

## Step 5: Open a PR that intentionally changes behavior

Create a branch and apply the canned patch:

```bash
git checkout -b demo/always-cite
git apply changes/always_cite.patch
git commit -am "Force citations into every response"
git push -u origin demo/always-cite
gh pr create --title "Force citations into every response" --body "Intentional change for Probegen quickstart"
```

That patch modifies [examples/langgraph-agentic-rag/prompts/answer.md](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/prompts/answer.md) so the assistant is pushed toward always-on citations, including on conversational or weakly grounded turns.

## Step 6: Inspect the generated Probegen artifacts

When the PR workflow finishes, look for:

- a PR comment from Probegen listing the proposed probes
- the uploaded workflow artifact containing `.probegen/stage1.json`, `.probegen/stage2.json`, and `.probegen/stage3.json`

Your exact wording will vary, but the shape should resemble:

- [examples/langgraph-agentic-rag/expected_outputs/stage1.json](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/expected_outputs/stage1.json)
- [examples/langgraph-agentic-rag/expected_outputs/stage2.json](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/expected_outputs/stage2.json)
- [examples/langgraph-agentic-rag/expected_outputs/stage3.json](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/expected_outputs/stage3.json)

What you want to see:

- Stage 1 understands the behavioral intent of the prompt change
- Stage 2 identifies that your existing eval set covers factual retrieval better than conversational citation boundaries
- Stage 3 proposes new samples around casual replies, unsupported questions, and ambiguous partial-overlap queries

## Step 7: Approve and merge

Add the label `probegen:approve` to the PR, then merge it.

The merge-time workflow will:

1. resolve the exact earlier analysis run for the PR head SHA
2. download the matching `.probegen` artifact from that run
3. check out the merged repo so `probegen.yaml` is available
4. write the approved probes into the mapped LangSmith dataset

This is the part that exercises the fixed write path.

## Step 8: Confirm probes were written back to LangSmith

Open the `acme-rag-baseline` dataset in LangSmith and confirm you now see new examples generated by Probegen.

Each written example should include metadata such as:

- `generated_by: probegen`
- `probe_type`
- `probe_id`
- `source_pr`

## What this quickstart pressure-tests well

- prompt-only behavior changes that should create new coverage
- conversational boundary regressions
- unsupported-question honesty
- weak-retrieval overcorrection
- end-to-end artifact handoff between PR analysis and merge-time writeback

## Good follow-up experiments

- change [examples/langgraph-agentic-rag/judges/retrieval_relevance.md](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/judges/retrieval_relevance.md) so the judge becomes too permissive
- remove the unsupported-question rule from [examples/langgraph-agentic-rag/prompts/answer.md](/Users/an/Documents/probeGen/examples/langgraph-agentic-rag/prompts/answer.md)
- add a new knowledge-base document and update the rewrite prompt to over-index on it

## Why this demonstrates Probegen better than a hello-world app

The point is not that the LangGraph app is complicated. The point is that a tiny prompt change should create realistic, reviewable, change-coupled eval work. This demo makes that visible with minimal setup noise.
