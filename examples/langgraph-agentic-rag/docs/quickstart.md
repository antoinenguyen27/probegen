# Quickstart: Real Probegen Test With A LangGraph Agentic RAG Demo

This quickstart gives you a real end-to-end Probegen exercise:

1. create your own GitHub repo from this example
2. connect it to LangSmith
3. confirm the Probegen GitHub Action is in place
4. open a PR that changes agent behavior across two prompts
5. inspect the generated gaps and probes
6. approve and merge the PR
7. confirm that the approved probes are written back to LangSmith

This example is based on LangGraph's agentic RAG pattern, but simplified so the evaluation story is the main event.

This quickstart intentionally uses a seeded LangSmith dataset so you can observe Probegen's coverage-aware mode. Probegen does not require an existing eval corpus to run; without one, it falls back to bootstrap mode and proposes starter evals from the diff and context pack.

## Why this example

This repo uses agentic RAG because it gives Probegen more useful evaluation surfaces without adding setup drag:

- retrieval vs conversational routing
- question rewriting
- relevance grading
- grounded answering
- unsupported-question handling
- citation boundaries

That combination makes it easier to generate multiple high-signal probes from a small prompt change. And because the demo patch touches two prompts — the routing step and the answer step — it also exercises Probegen's compound change detection.

## Getting started: create your own repository

If you have not yet copied this example into its own GitHub repo, do that first. From the root of the Probegen repository:

```bash
cp -R examples/langgraph-agentic-rag /tmp/acme-rag-probegen-demo
cd /tmp/acme-rag-probegen-demo
git init
git add .
git commit -m "Initial LangGraph RAG demo"
gh repo create acme-rag-probegen-demo --private --source=. --push
```

If you are already reading this from inside your copied repo, you have completed this step.

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

Populate `OPENAI_API_KEY` in `.env` (the other variables can wait until later steps), then run:

```bash
python -m app.main "How long are exports available after I create one?"
python -m app.main "Thanks for the help"
```

Baseline behavior should look like this:

- factual product questions retrieve a knowledge-base document and cite it
- casual conversational replies stay conversational and do not force citations

These two queries are specifically chosen to demonstrate the citation-routing boundary that `changes/proactive_retrieval.patch` targets. The patch removes the conversational retrieval carve-out and adds a proactive-surfacing rule, creating regressions on exactly these two cases.

## Step 2: Seed the baseline eval dataset in LangSmith

The example expects a LangSmith dataset named `acme-rag-baseline`, which is already wired in [probegen.yaml](../probegen.yaml).

Get a LangSmith API key from [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys. Add it to your `.env` file (`LANGSMITH_API_KEY=...`), then run:

```bash
python scripts/seed_langsmith_dataset.py
```

This creates a small baseline eval set with factual retrieval coverage and one unsupported-question case, intentionally leaving citation-boundary and proactive-surfacing gaps. The script is idempotent — running it twice will not create duplicates.

## Step 3: Configure GitHub secrets and create the approval label

In your GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

- `ANTHROPIC_API_KEY`
- `LANGSMITH_API_KEY`

You do not need `OPENAI_API_KEY` for the Probegen CI workflow because Probegen does not run the app itself.

Then create the approval label. Go to **Issues → Labels → New label** and create a label named exactly `probegen:approve` (any color). Probegen's merge-time workflow only fires when a PR is merged with this label — GitHub does not create unknown labels automatically, so you must create it before running the demo.

## Step 4: Review the Probegen config and workflow

This example already includes everything Probegen needs — you do not need to create any of these files:

- [probegen.yaml](../probegen.yaml) — tells Probegen which files to watch and where to write probes
- [.github/workflows/probegen.yml](../.github/workflows/probegen.yml) — the GitHub Actions workflow
- [context/](../context/) — the context pack Probegen reads to understand the agent

The key detail in [probegen.yaml](../probegen.yaml) is the `mappings` section. It wires each prompt file to the `acme-rag-baseline` LangSmith dataset. This is what lets Stage 2 run in coverage-aware mode — it knows which existing eval cases apply to each changed prompt.

## Step 5: Open a PR that intentionally changes behavior

Create a branch and apply the canned patch:

```bash
git checkout -b demo/proactive-retrieval
git apply changes/proactive_retrieval.patch
git commit -am "Make assistant more proactive about surfacing relevant context"
git push -u origin demo/proactive-retrieval
gh pr create --title "Make assistant more proactive about surfacing relevant context" --body "Intentional change for Probegen quickstart"
```

The patch modifies two files:

- [prompts/query_or_respond.md](../prompts/query_or_respond.md): removes the explicit exception that allowed conversational turns to skip retrieval, and strengthens the retrieval bias
- [prompts/answer.md](../prompts/answer.md): adds a rule to proactively surface related documentation after answering

Both changes are individually plausible (a developer wanting to be more helpful), but together they create non-obvious regressions that Probegen's Stage 1 will detect as a compound change.

## Step 6: Inspect the generated Probegen artifacts

When the PR workflow finishes, look for:

- a PR comment from Probegen listing the proposed probes
- the uploaded workflow artifact containing `.probegen/stage1.json`, `.probegen/stage2.json`, and `.probegen/stage3.json`

Your exact wording will vary, but the shape should resemble:

- [expected_outputs/stage1.json](../expected_outputs/stage1.json)
- [expected_outputs/stage2.json](../expected_outputs/stage2.json)
- [expected_outputs/stage3.json](../expected_outputs/stage3.json)

What you want to see:

- Stage 1 flags `compound_change_detected: true` with two entries in `changes[]`
- Stage 2 identifies that your existing eval set covers factual retrieval but not conversational routing or proactive-surfacing behavior
- Stage 3 proposes probes around casual replies, scope-creep on factual answers, and unsupported questions under proactive surfacing

## Step 7: Approve and merge

In the PR sidebar on GitHub, click **Labels** and add the `probegen:approve` label you created in Step 3. Then merge the PR.

The merge-time workflow will:

1. resolve the exact earlier analysis run for the PR head SHA
2. download the matching `.probegen` artifact from that run
3. check out the merged repo so `probegen.yaml` is available
4. write the approved probes into the mapped LangSmith dataset

## Step 8: Confirm probes were written back to LangSmith

Go to [smith.langchain.com](https://smith.langchain.com), open the `acme-rag-baseline` dataset, and confirm you now see new examples generated by Probegen.

Each written example should include metadata such as:

- `generated_by: probegen`
- `probe_type`
- `probe_id`
- `source_pr`

## What this quickstart pressure-tests well

- compound prompt changes that should create new coverage across multiple components
- conversational routing regressions
- proactive-surfacing scope creep
- unsupported-question honesty under a proactive-surfacing rule
- end-to-end artifact handoff between PR analysis and merge-time writeback

## Good follow-up experiments

- change [prompts/grade.md](../prompts/grade.md) so the relevance grader becomes too permissive — Probegen will detect the changed grading logic as a behavioral change
- remove the unsupported-question rule from [prompts/answer.md](../prompts/answer.md)
- add a new knowledge-base document and update [prompts/rewrite.md](../prompts/rewrite.md) to over-index on it

## Why this demonstrates Probegen better than a hello-world app

The point is not that the LangGraph app is complicated. The point is that a tiny prompt change — one that sounds like an improvement — should create realistic, reviewable, change-coupled eval work. This demo makes that visible with minimal setup noise.
