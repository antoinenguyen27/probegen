# Quickstart: Parity End-to-End Demo with LangGraph Agentic RAG

This quickstart walks you through a complete Parity workflow using a real LangGraph app as the target. By the end you will have:

1. A working agentic RAG app running locally
2. A seeded LangSmith baseline eval dataset
3. A PR that introduces a single prompt addition with a non-obvious risk
4. Parity's Stage 1–3 artifacts: detected changes, coverage gaps, and proposed evals
5. Evals written back to LangSmith after merge approval

The LangGraph app is a close implementation of the [LangGraph agentic RAG reference pattern](https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_agentic_rag/): an agent that retrieves from Lilian Weng's ML research blog posts, grades document relevance, rewrites queries when retrieval misses, and generates grounded answers.

---

## How this demo is structured

The app has one file that matters to Parity: `app/graph.py`. All agent behavior is defined there as inline Python string constants (`GRADE_PROMPT`, `REWRITE_PROMPT`, `GENERATE_PROMPT`) and as node functions. There are no separate prompt files — this is intentional. It lets Parity demonstrate that it detects behavioral changes in Python constants, not just in dedicated prompt files.

### About the `parity.yaml` configuration

The `parity.yaml` file in this demo defines **hint patterns** that guide Parity's discovery:

- **`behavior_artifacts.paths: ["app/**"]`** — tells Parity to pre-load files in the `app/` directory for efficiency
- **`python_patterns`** — hints that the agent should look for Python module-level constants with these naming patterns

**Important:** These patterns are *discovery hints*, not filters. The Stage 1 agent always sees **all changed files** in the PR and can inspect any of them using `Read`, `Glob`, and a read-only Bash surface for git inspection. It can also inspect unchanged supporting files elsewhere in the repo when they are needed to understand how a changed artifact is used. Files matching the configured patterns are pre-loaded with before/after content and diffs to optimize performance. Files that don't match are still visible to the agent — it just needs to fetch them on-demand.

In this demo, the changes happen to be in `app/graph.py`, which matches the configured pattern, so they're pre-loaded. But if you modified a file outside `app/` with behavioral significance, Parity's agent would still detect it.

The demo patch (`changes/always_cite.patch`) modifies one constant:

- **`GENERATE_PROMPT`**: adds two sentences requiring the generator to cite the source blog post for each claim, and to avoid fabricating a source when the origin cannot be determined

The change sounds entirely reasonable — citations improve transparency. The non-obvious risk is that `retrieve_blog_posts` returns raw `page_content` only, with no source metadata. The model must infer which blog post a chunk came from based on text alone, and may fabricate or misattribute citations when chunks are ambiguous. Parity's Stage 1 flags this gap. Stage 2 identifies that no existing baseline cases test citation presence or accuracy. Stage 3 proposes evals targeting those specific gaps.

---

## Prerequisites

- Python 3.11+
- Node.js 22+ (used by the GitHub Actions workflow; installed automatically in CI)
- Git and the `gh` CLI
- An OpenAI API key — the app uses `gpt-4.1` for all LLM calls and OpenAI embeddings for the vector store
- An Anthropic API key — Parity uses Claude for Stages 1, 2, and 3
- A LangSmith account and API key — used for the baseline eval dataset and eval writeback

---

## Step 1: Create your own GitHub repository from this example

Parity runs as a GitHub Actions workflow, so the demo needs to live in its own repo. Create one from the template:

```bash
gh repo create my-rag-demo --template antoinenguyen27/parity-langgraph-example --clone --private
cd my-rag-demo
```

This creates a private copy with all the example files — no need to clone the full Parity repository.

---

## Step 2: Install dependencies and set up environment

```bash
python -m venv .venv 
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in all three API keys:

- **`ANTHROPIC_API_KEY`** — Required by Parity (not this app)
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

The demo expects a LangSmith dataset named `lilian-weng-rag-baseline`. This is already referenced in `parity.yaml` under `evals.rules`. Without it, Stage 2 may still run, but it is more likely to fall back to bootstrap discovery instead of resolving the intended target directly.

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

In your GitHub repo, go to **Settings → Secrets and variables → Actions** and add into your Repository Secrets:

| Secret | Required for |
|---|---|
| `ANTHROPIC_API_KEY` | Stages 1, 2, and 3 |
| `OPENAI_API_KEY` | Stage 2 eval analysis (`embed-batch` compares candidate eval inputs against the LangSmith dataset using OpenAI embeddings) |
| `LANGSMITH_API_KEY` | Stage 2 dataset query and deterministic writeback |

Then create the approval label:

```bash
gh label create "parity:approve" --color 0075ca --description "Approve Parity eval writeback"
```

Or go to **Issues → Labels → New label** and create a label named exactly `parity:approve`. Parity's merge-time workflow (`parity-write` job) only fires when a PR is merged with this label. GitHub does not create unknown labels automatically.

---

## Step 6: Review the Parity config and workflow

This repo already contains everything Parity needs. You do not need to create any of these files.

**`parity.yaml`** — the Parity configuration:

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

The `evals.rules` section gives Parity a preferred LangSmith target for `app/graph.py`. Stage 2 still performs topology discovery, but it starts from this preferred target instead of treating the repo as fully unstructured.

```yaml
evals:
  rules:
    - artifact: "app/graph.py"
      preferred_platform: langsmith
      preferred_target: "lilian-weng-rag-baseline"
```

**`.github/workflows/parity.yml`** — the GitHub Actions workflow with three jobs:

- `parity-analyze`: runs on every PR, executes Stages 1–3, uploads artifacts
- `parity-comment`: posts the PR comment from the saved Stage 3 artifact
- `parity-write`: runs on merge when the `parity:approve` label is present, downloads the Stage 3 artifact, writes native-ready evals, and posts a merged-PR writeback result comment

---

## Step 7: Open the demo PR

Create a branch and apply the demo patch:

```bash
git checkout -b demo/always-cite
git apply changes/always_cite.patch
git commit -am "Add citation instructions to answer generation prompt"
git push -u origin demo/always-cite
gh pr create \
  --title "Add citation instructions to answer generation prompt" \
  --body "Instructs the generator to cite the source blog post for each claim, and to avoid fabricating a source when the origin cannot be determined."
```

**What the patch changes in `app/graph.py`:**

`GENERATE_PROMPT` before:
> If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise.

`GENERATE_PROMPT` after:
> If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise. For each claim you make, cite the blog post it comes from (e.g., 'According to the hallucination post, ...'). If you cannot identify the source, do not fabricate one.

The change is individually plausible. The risk — that the retriever passes only raw text with no source metadata, leaving the model to infer or fabricate citations — is not visible from reading the prompt diff alone.

---

## Step 8: Inspect the Parity artifacts

When the `parity-analyze` workflow completes (~2–4 minutes), look for:

- A PR comment from Parity listing proposed evals
- The uploaded workflow artifact containing `.parity/stage1.json`, `.parity/stage2.json`, and `.parity/stage3.json`

Compare your output against the reference examples in [`expected_outputs/`](../expected_outputs/). The exact wording will differ, but the structure should match:

**Stage 1 (`stage1.json`)** — should show `compound_change_detected: false` with one entry in `changes[]`:
- `GENERATE_PROMPT` change: `artifact_type: python_variable`, inferred intent is per-claim citation of source blog posts
- Two `unintended_risk_flags`: one about missing source metadata in the retriever, one about the citation instruction potentially conflicting with the three-sentence conciseness constraint

**Stage 2 (`stage2.json`)** — should show the resolved target, its discovered eval method profile, and the coverage gaps tied to that target.

The Stage 2 analysis should show `coverage_ratio: 0.0` for the discovered target (no existing baseline cases test citation behavior at all). Three gaps should appear:
- `gap_001` (uncovered): no case checks whether generated answers include a citation or whether any citation is accurate
- `gap_002` (boundary_shift): no case tests citation accuracy when retrieved chunks span multiple blog posts in a single response
- `gap_003` (uncovered): no case tests whether the model actually refrains from fabricating a source when chunk text is ambiguous

**Stage 3 (`stage3.json`)** — should propose native eval intents plus renderings in ascending order of difficulty:
- A `regression_guard` with a single-source question (one clear answer in one post)
- A `boundary_probe` with a cross-post question
- An `overcorrection_probe` with an ambiguous question

---

## Step 9: Approve and merge

Add the `parity:approve` label to the PR **before merging**. In the PR sidebar on GitHub, click **Labels** and select `parity:approve`. Then merge the PR.

The `parity-write` job will:

1. Identify the earlier `parity-analyze` run for the PR head SHA using `parity resolve-run-id`
2. Download the matching `.parity` artifact from that run
3. Check out the merged repo so `parity.yaml` is available
4. Write the approved evals to `lilian-weng-rag-baseline` in LangSmith
5. Post a merged-PR result comment summarizing the writeback outcome

---

## Step 10: Confirm evals were written to LangSmith

Go to [smith.langchain.com](https://smith.langchain.com), open the `lilian-weng-rag-baseline` dataset, and confirm you see new examples added by Parity.

Each written example will include metadata such as:

```json
{
  "generated_by": "parity",
  "intent_type": "boundary_probe",
  "rendering_id": "render-intent_002",
  "source_pr": 1
}
```

---

## What this demo pressure-tests

- **Non-obvious prompt risk**: the citation instruction looks correct in isolation; the risk only becomes visible when you trace what the retriever actually passes to the generator
- **Coverage-aware analysis**: Stage 2 has a real baseline to compare against and identifies specific gaps rather than generating from scratch
- **Python constant detection**: prompts are inline strings in `app/graph.py`, not `.md` files — Parity finds them via `python_patterns: ["*_PROMPT"]`
- **Cross-post attribution cases**: the three blog posts share vocabulary, so citation accuracy depends on the model correctly inferring source identity from raw chunk text
- **End-to-end artifact handoff**: the merge-time job downloads the exact artifact from the PR analysis run, not a recomputed version

---

## Follow-up experiments

- **Change `REWRITE_PROMPT`** to over-index on the most recent question in a multi-turn conversation — Stage 1 will detect it as a query-rewriting behavior change
- **Remove the `grade_documents` structured output schema** (`GradeDocuments`) and replace with free-text reasoning — Stage 1 will flag the classifier schema removal
- **Add a fourth blog post URL** to `app/graph.py` and update `GENERATE_PROMPT` to reference it — Stage 1 will detect a compound change in both retrieval scope and generation behavior
- **Revert the patch after writing evals**, then open a new PR with the same patch — Stage 2 will now find the previously written evals as existing coverage and should propose fewer or more precisely targeted evals, demonstrating the feedback loop

---

## Why this example works better than a hello-world app

A single-prompt hello-world app produces one behavioral change and one obvious eval. The point of this demo is different: a small, individually plausible prompt change should create realistic eval gaps that are genuinely hard to notice without tooling. This app is just complex enough to make that visible — three blog posts, four graph nodes, three prompt constants — without adding setup drag.
