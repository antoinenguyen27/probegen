# Quickstart: Probegen End-to-End Demo with LangGraph Agentic RAG

This quickstart walks you through a complete Probegen workflow using a real LangGraph app as the target. By the end you will have:

1. A working agentic RAG app running locally
2. A seeded LangSmith baseline eval dataset
3. A PR that introduces a compound behavioral regression
4. Probegen's Stage 1–3 artifacts: detected changes, coverage gaps, and proposed probes
5. Probes written back to LangSmith after merge approval

The LangGraph app is a close implementation of the [LangGraph agentic RAG reference pattern](https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_agentic_rag/): an agent that retrieves from Lilian Weng's ML research blog posts, grades document relevance, rewrites queries when retrieval misses, and generates grounded answers.

---

## How this demo is structured

The app has one file that matters to Probegen: `app/graph.py`. All agent behavior is defined there as inline Python string constants (`GRADE_PROMPT`, `REWRITE_PROMPT`, `GENERATE_PROMPT`) and as node functions. There are no separate prompt files — this is intentional. It lets Probegen demonstrate that it detects behavioral changes in Python constants, not just in dedicated prompt files.

The demo patch (`changes/always_cite.patch`) modifies two of those constants in a single PR:

- **`GRADE_PROMPT`**: relaxes the relevance grader from strict keyword/semantic matching to loose topical/thematic matching with a bias toward "yes" when uncertain
- **`GENERATE_PROMPT`**: removes both the "say you don't know" instruction and the three-sentence conciseness constraint, replacing them with "provide a thorough and complete answer"

Each change sounds reasonable in isolation. Together they create a compound regression: the permissive grader lets loosely matched passages through, and the generator then produces verbose confident answers from that weak context instead of admitting uncertainty. Probegen's Stage 1 flags this as a compound change. Stage 2 identifies that the seeded baseline only partially covers the affected behavior. Stage 3 proposes probes targeting the specific gaps.

---

## Prerequisites

- Python 3.11+
- Node.js 22+ (used by the GitHub Actions workflow; installed automatically in CI)
- Git and the `gh` CLI
- An OpenAI API key — the app uses `gpt-4.1` for all LLM calls and OpenAI embeddings for the vector store
- An Anthropic API key — Probegen uses Claude for Stages 1, 2, and 3
- A LangSmith account and API key — used for the baseline eval dataset and probe writeback

---

## Step 1: Create your own GitHub repository from this example

Probegen runs as a GitHub Actions workflow, so the demo needs to live in its own repo. From the root of the Probegen repository:

```bash
cp -R examples/langgraph-agentic-rag /tmp/lilian-weng-rag-demo
cd /tmp/lilian-weng-rag-demo
git init
git add .
git commit -m "Initial LangGraph agentic RAG demo"
gh repo create lilian-weng-rag-probegen-demo --private --source=. --push
```

If you are reading this from inside a repo you already copied, skip this step.

---

## Step 2: Install dependencies and set up environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in all three API keys:

- **`ANTHROPIC_API_KEY`** — Required by Probegen (not this app)
- **`OPENAI_API_KEY`** — Required by the app (all LLM calls and embeddings)
- **`LANGSMITH_API_KEY`** — Required because tracing is enabled; without it, the app will fail with auth errors

Get your keys from:
- Anthropic: [console.anthropic.com → API Keys](https://console.anthropic.com/account/keys)
- OpenAI: [platform.openai.com → API Keys](https://platform.openai.com/account/api-keys)
- LangSmith: [smith.langchain.com → Settings → API Keys](https://smith.langchain.com)

The `.env.example` file also includes an optional `USER_AGENT` variable, already set to a sensible default. This silences warnings when the app fetches the blog URLs.

**Why all three API keys upfront?** The app has `LANGSMITH_TRACING=true` enabled by default. If you skip the LangSmith key, tracing will fail with a 401 error. It's cleaner to set all keys now than to debug auth failures later.

---

## Step 3: Smoke-test the app

Run two queries that exercise the two behavioral paths this demo targets:

```bash
# Factual question — should trigger retrieval and return a grounded answer
python -m app.main "What does Lilian Weng say about types of reward hacking?"

# Conversational turn — should respond directly without retrieval
python -m app.main "Thanks, that was helpful"
```

**Expected baseline behavior:**

- The factual question retrieves from the reward-hacking blog post and returns something like: *"Lilian Weng categorises reward hacking into two types: environment or goal misspecification, and reward tampering."* The answer is concise (three sentences or fewer).
- The conversational turn gets a direct friendly reply with no retrieval call and no citations.

These two queries are specifically chosen because the demo patch creates regressions on both: the permissive grader may let unrelated passages through on factual questions, and the verbose generator removes the conciseness guarantee.

---

## Step 4: Seed the baseline eval dataset in LangSmith

The demo expects a LangSmith dataset named `lilian-weng-rag-baseline`. This is already referenced in `probegen.yaml` under `mappings`. Without it, Stage 2 cannot run in coverage-aware mode and will fall back to starter mode.

Seed the dataset:

```bash
python scripts/seed_langsmith_dataset.py
```

This creates five eval cases in `lilian-weng-rag-baseline`:

| ID | What it tests |
|---|---|
| `lilian-reward-hacking-types` | Factual retrieval: two types of reward hacking |
| `lilian-hallucination-factscore` | Factual retrieval: FActScore methodology |
| `lilian-diffusion-lumiere` | Factual retrieval: Lumiere vs. cascade approaches |
| `lilian-reward-sycophancy-followup` | Multi-turn: sycophancy as reward hacking (follow-up) |
| `lilian-unsupported-moe` | Unsupported question: MoE architectures not in the blog |

The script is idempotent — running it twice does not create duplicates. Verify the dataset in LangSmith before continuing.

**What the baseline intentionally leaves uncovered:** Questions that exploit keyword overlap across blog posts (e.g., "temporal" appears in both the hallucination and diffusion-video posts) and questions that test answer length compliance. These are the gaps the demo patch exposes.

---

## Step 5: Configure GitHub secrets and create the approval label

In your GitHub repo, go to **Settings → Secrets and variables → Actions** and add:

| Secret | Required for |
|---|---|
| `ANTHROPIC_API_KEY` | Stages 1, 2, and 3 |
| `OPENAI_API_KEY` | Stage 2 coverage-aware mode (`embed-batch` compares probe candidates against the LangSmith dataset using OpenAI embeddings) |
| `LANGSMITH_API_KEY` | Stage 2 dataset query and Stage 4 probe writeback |

Then create the approval label:

```bash
gh label create "probegen:approve" --color 0075ca --description "Approve Probegen probe writeback"
```

Or go to **Issues → Labels → New label** and create a label named exactly `probegen:approve`. Probegen's merge-time workflow (`probegen-write` job) only fires when a PR is merged with this label. GitHub does not create unknown labels automatically.

---

## Step 6: Review the Probegen config and workflow

This repo already contains everything Probegen needs. You do not need to create any of these files.

**`probegen.yaml`** — the Probegen configuration:

```yaml
behavior_artifacts:
  paths:
    - "app/**"          # watch all files under app/
  python_patterns:
    - "*_PROMPT"        # match module-level constants like GRADE_PROMPT, GENERATE_PROMPT
    - "*_prompt"
    - "*_instruction"
    - "system_*"
    - "*_template"
```

The `mappings` section wires `app/graph.py` to the `lilian-weng-rag-baseline` LangSmith dataset. This is what enables Stage 2 to run in coverage-aware mode — it knows which existing eval cases apply to changes in that file.

```yaml
mappings:
  - artifact: "app/graph.py"
    platform: langsmith
    dataset: "lilian-weng-rag-baseline"
```

**`.github/workflows/probegen.yml`** — the GitHub Actions workflow with two jobs:

- `probegen-analyze`: runs on every PR, executes Stages 1–3, posts a comment, uploads artifacts
- `probegen-write`: runs on merge when the `probegen:approve` label is present, downloads the Stage 3 artifact and writes probes to LangSmith

---

## Step 7: Open the demo PR

Create a branch and apply the demo patch:

```bash
git checkout -b demo/always-cite
git apply changes/always_cite.patch
git commit -am "Relax grading threshold and make answers more thorough"
git push -u origin demo/always-cite
gh pr create \
  --title "Relax grading threshold and make answers more thorough" \
  --body "Makes the relevance grader more permissive and removes the conciseness constraint from the generator."
```

**What the patch changes in `app/graph.py`:**

`GRADE_PROMPT` before:
> If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant. Give a binary score 'yes' or 'no'.

`GRADE_PROMPT` after:
> If the document has **any topical or thematic connection** to the user question, grade it as relevant. **When in doubt, prefer to grade the document as relevant rather than irrelevant.** Give a binary score 'yes' or 'no'.

`GENERATE_PROMPT` before:
> If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise.

`GENERATE_PROMPT` after:
> Provide a **thorough and complete answer** using all available context.

Both changes are individually plausible developer decisions. The compound regression — verbose confident answers from loosely matched context — is not obvious from reading either diff in isolation.

---

## Step 8: Inspect the Probegen artifacts

When the `probegen-analyze` workflow completes (~2–4 minutes), look for:

- A PR comment from Probegen listing proposed probes
- The uploaded workflow artifact containing `.probegen/stage1.json`, `.probegen/stage2.json`, and `.probegen/stage3.json`

Compare your output against the reference examples in [`expected_outputs/`](../expected_outputs/). The exact wording will differ, but the structure should match:

**Stage 1 (`stage1.json`)** — should show `compound_change_detected: true` with two entries in `changes[]`:
- `GRADE_PROMPT` change: `artifact_type: python_variable`, inferred intent is relaxed relevance grading with a permissive bias
- `GENERATE_PROMPT` change: removed uncertainty admission, removed conciseness constraint
- The `compound_changes` array explains how the two interact: permissive grading passes weak context to a generator that no longer admits uncertainty

**Stage 2 (`stage2.json`)** — should show `coverage_ratio: 0.4` (2 of 5 baseline cases cover the changed behavior). Three gaps should appear:
- `gap_001` (uncovered): no case tests the combined failure — permissive grading passes weak context and the generator produces a confident verbose answer instead of admitting uncertainty
- `gap_002` (boundary_shift): no case tests keyword-overlap misrouting, where a word like "temporal" appears in both the hallucination and diffusion-video posts and triggers the wrong passage under the permissive grader
- `gap_003` (uncovered): no case asserts answer length compliance now that the three-sentence constraint is removed

**Stage 3 (`stage3.json`)** — should propose three probes:
- A `boundary_probe` targeting keyword-overlap confusion (e.g., a question about temporal attention in transformers, where "temporal" is a false signal that the permissive grader accepts)
- An `overcorrection_probe` targeting cross-post misrouting (a question where a passage from the wrong blog post could produce a wrong confident answer)
- A `regression_guard` targeting answer length on a question that previously returned a concise two-sentence response

---

## Step 9: Approve and merge

Add the `probegen:approve` label to the PR **before merging**. In the PR sidebar on GitHub, click **Labels** and select `probegen:approve`. Then merge the PR.

The `probegen-write` job will:

1. Identify the earlier `probegen-analyze` run for the PR head SHA using `probegen resolve-run-id`
2. Download the matching `.probegen` artifact from that run
3. Check out the merged repo so `probegen.yaml` is available
4. Write the approved probes to `lilian-weng-rag-baseline` in LangSmith

---

## Step 10: Confirm probes were written to LangSmith

Go to [smith.langchain.com](https://smith.langchain.com), open the `lilian-weng-rag-baseline` dataset, and confirm you see new examples added by Probegen.

Each written example will include metadata such as:

```json
{
  "generated_by": "probegen",
  "probe_type": "boundary_probe",
  "probe_id": "probe_001",
  "source_pr": "1"
}
```

---

## What this demo pressure-tests

- **Compound prompt changes**: two prompt constants changed in one PR; neither change alone predicts the regression
- **Coverage-aware mode**: Stage 2 has a real baseline to compare against and identifies specific gaps rather than generating from scratch
- **Python constant detection**: prompts are inline strings in `app/graph.py`, not `.md` files — Probegen finds them via `python_patterns: ["*_PROMPT"]`
- **Keyword-overlap boundary cases**: the three blog posts share vocabulary (e.g., "temporal") that creates false relevance signals under the permissive grader
- **End-to-end artifact handoff**: the merge-time job downloads the exact artifact from the PR analysis run, not a recomputed version

---

## Follow-up experiments

- **Change `REWRITE_PROMPT`** to over-index on the most recent question in a multi-turn conversation — Stage 1 will detect it as a query-rewriting behavior change
- **Remove the `grade_documents` structured output schema** (`GradeDocuments`) and replace with free-text reasoning — Stage 1 will flag the classifier schema removal
- **Add a fourth blog post URL** to `app/graph.py` and update `GENERATE_PROMPT` to reference it — Stage 1 will detect a compound change in both retrieval scope and generation behavior
- **Revert the patch after writing probes**, then open a new PR with the same patch — Stage 2 will now find the previously written probes as existing coverage and should propose fewer or more precisely targeted probes, demonstrating the feedback loop

---

## Why this example works better than a hello-world app

A single-prompt hello-world app produces one behavioral change and one probe. The point of this demo is different: a small, individually plausible prompt change should create realistic, compound, change-coupled eval work that is genuinely hard to notice without tooling. This app is just complex enough to make that visible — three blog posts, four graph nodes, three prompt constants — without adding setup drag.
