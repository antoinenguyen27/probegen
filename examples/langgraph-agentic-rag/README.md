# LangGraph Agentic RAG Example

This example is a deliberately small repository that makes Probegen's PR-to-probe workflow easy to test with a real LangGraph app.

It is adapted from LangGraph's agentic RAG pattern, but the emphasis here is not on app complexity. The emphasis is on making behavioral changes easy to observe, easy to map to an eval dataset, and easy for Probegen to turn into targeted probes.

This demo is deliberately coverage-aware: it seeds a small LangSmith dataset so Stage 2 has something real to compare against. Probegen itself can also run without that baseline, in which case it falls back to bootstrap probe generation.

## What this demo is built to show

1. A baseline LangGraph RAG agent that answers from a small knowledge base.
2. A seeded LangSmith dataset with partial coverage.
3. A prompt-only change that creates non-obvious evaluation gaps across two behavior artifacts.
4. The full Probegen workflow:
   - detect the behavior change (including compound multi-artifact changes),
   - compare it to existing eval coverage,
   - propose new probes in the PR,
   - write approved probes back to LangSmith after merge.

## Repository layout

- `app/`: the LangGraph app
- `knowledge_base/`: local markdown files used for retrieval
- `prompts/`: all behavior-defining prompts that Probegen watches — routing, grading, rewriting, and answering
- `context/`: Probegen context pack
- `docs/quickstart.md`: step-by-step walkthrough with explanations of each stage
- `.github/workflows/probegen.yml`: GitHub Actions workflow
- `scripts/seed_langsmith_dataset.py`: seeds the baseline eval dataset
- `changes/proactive_retrieval.patch`: intentional PR change used in the demo
- `expected_outputs/`: illustrative Stage 1-3 artifacts for the patch

## Fast path

### 1. Copy this directory into its own Git repository

```bash
cp -R examples/langgraph-agentic-rag /tmp/acme-rag-probegen-demo
cd /tmp/acme-rag-probegen-demo
git init
git add .
git commit -m "Initial LangGraph RAG demo"
gh repo create acme-rag-probegen-demo --private --source=. --push
```

### 2. Install dependencies and smoke test the app

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Populate `OPENAI_API_KEY` in `.env` (the other variables can wait until later steps), then run:

```bash
python -m app.main "How long are exports available after I create one?"
python -m app.main "Thanks for the help"
```

These two queries demonstrate the citation-routing boundary that `changes/proactive_retrieval.patch` targets. The first should retrieve and cite a knowledge-base document. The second should reply naturally without retrieval or citations. After applying the patch, the routing change and proactive-surfacing rule create regressions on exactly these two cases — which is what Probegen detects and writes probes for.

### 3. Seed the baseline eval dataset in LangSmith

Add `LANGSMITH_API_KEY` to your `.env` file (get one from [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys), then run:

```bash
python scripts/seed_langsmith_dataset.py
```

This creates the `acme-rag-baseline` dataset with factual retrieval coverage and one unsupported-question case, intentionally leaving citation-boundary and proactive-surfacing gaps. The script is idempotent — running it twice will not create duplicates.

### 4. Add GitHub secrets and create the approval label

In your new GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

- `ANTHROPIC_API_KEY` — for Probegen's generation stages
- `LANGSMITH_API_KEY` — for eval dataset access and merge-time writeback

You do not need `OPENAI_API_KEY` in CI; Probegen does not run the app itself.

Then create the approval label. Go to **Issues → Labels → New label**, create a label named exactly `probegen:approve` (any color). Probegen's merge-time workflow only fires when this label is present on a merged PR — GitHub does not create unknown labels automatically, so you must create it first.

### 5. Open a PR that intentionally changes behavior

```bash
git checkout -b demo/proactive-retrieval
git apply changes/proactive_retrieval.patch
git commit -am "Make assistant more proactive about surfacing relevant context"
git push -u origin demo/proactive-retrieval
gh pr create \
  --title "Make assistant more proactive about surfacing relevant context" \
  --body "Intentional change for Probegen demo"
```

The patch modifies two prompts: `prompts/query_or_respond.md` (removes the conversational retrieval carve-out, strengthens retrieval bias) and `prompts/answer.md` (adds a proactive-surfacing rule). Both modifications are individually plausible, but together they create non-obvious regressions that Probegen's Stage 1 detects as a compound change.

### 6. Review the generated probes

When the PR workflow finishes:

- a PR comment from Probegen lists the proposed probes
- the uploaded workflow artifact contains `.probegen/stage1.json`, `.probegen/stage2.json`, and `.probegen/stage3.json`

The shape should resemble `expected_outputs/stage1.json`, `stage2.json`, and `stage3.json` in this directory. What to look for:

- Stage 1 detects two changed artifacts and flags `compound_change_detected: true`
- Stage 2 identifies that the dataset covers factual retrieval but not conversational routing or proactive-surfacing behavior
- Stage 3 proposes probes targeting conversational-turn regressions, scope-creep on factual answers, and unsupported questions under proactive surfacing

### 7. Approve and merge

In the PR sidebar on GitHub, click **Labels** and add the `probegen:approve` label you created in step 4. Then merge the PR. The merge-time workflow automatically resolves the earlier analysis run for the PR's head SHA, downloads the matching `.probegen` artifact, and writes the approved probes into the `acme-rag-baseline` LangSmith dataset.

### 8. Confirm probes were written to LangSmith

Go to [smith.langchain.com](https://smith.langchain.com), open the `acme-rag-baseline` dataset, and confirm new examples appear with metadata including `generated_by: probegen`, `probe_type`, `probe_id`, and `source_pr`.

---

For a detailed explanation of each step and how Probegen works, see [docs/quickstart.md](docs/quickstart.md).
