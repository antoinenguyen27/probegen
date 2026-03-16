# Probegen — Technical Specification

**Version:** 1.1
**Status:** Implemented
**Audience:** Engineers working on or integrating with Probegen

---

## Table of Contents

1. [What Is Probegen](#1-what-is-probegen)
2. [Purpose and Problem Statement](#2-purpose-and-problem-statement)
3. [Why This Approach](#3-why-this-approach)
4. [System Architecture Overview](#4-system-architecture-overview)
5. [Context Pack](#5-context-pack)
6. [Pipeline: Stage 1 — Change Detection and Intent Analysis](#6-pipeline-stage-1)
7. [Pipeline: Stage 2 — Coverage Gap Analysis](#7-pipeline-stage-2)
8. [Pipeline: Stage 3 — Probe Generation and Proposal](#8-pipeline-stage-3)
9. [Pipeline: Stage 4 — Platform Write and Auto-Run](#9-pipeline-stage-4)
10. [Approval Mechanism](#10-approval-mechanism)
11. [Eval Platform Integrations](#11-eval-platform-integrations)
12. [GitHub Action and CI Integration](#12-github-action-and-ci-integration)
13. [User Setup and Configuration](#13-user-setup-and-configuration)
14. [Data Models and Schemas](#14-data-models-and-schemas)
15. [Error Handling and Failure Modes](#15-error-handling-and-failure-modes)
16. [Out of Scope (v1)](#16-out-of-scope-v1)

---

## 1. What Is Probegen

Probegen is a CI-integrated developer tool for teams building LLM agents and prompt-driven systems. It detects behaviorally significant changes in a pull request — changes to prompts, agent instructions, guardrails, validators, and related artifacts — and automatically generates targeted evaluation probes designed to test the behavioral consequences of those specific changes.

Probegen is not an eval runner. It does not execute evals. It generates eval *inputs* — targeted test cases — that developers review and add to their existing evaluation pipelines. Its output feeds into whatever eval infrastructure the team already uses: LangSmith, Braintrust, Arize Phoenix, Promptfoo, or plain files. If no eval corpus exists yet, Probegen still operates in bootstrap mode and proposes plausible starter evals grounded in the diff and available product context.

Probegen runs as a non-blocking parallel job in GitHub Actions. It does not gate or delay merges. It surfaces a PR comment containing a ranked, rationale-annotated set of proposed eval probes for developer review, and writes approved probes to the eval platform after an explicit human approval step.

Probegen should be framed as working out of the box, while improving with more coverage and more context. Like many LLM applications, the more evals teams already have and the more detail they provide about product behavior, users, and failure modes, the better the generated recommendations become.

---

## 2. Purpose and Problem Statement

### The Core Problem

Teams building LLM agents iterate on prompts, instructions, guardrails, and tool configurations continuously. Every one of these changes modifies agent behavior — sometimes in the intended direction, sometimes not. The standard practice is to maintain an eval dataset and run it in CI. The problem is structural: **eval datasets are authored once, rarely expanded, and are almost never updated in proportion to the behavioral changes being made.**

When a developer changes a system prompt to fix a citation bug, the existing evals test what was important three months ago. The new edge cases introduced by the change — the register-scoping ambiguity, the overcorrection into non-factual contexts, the boundary between what triggers a citation and what doesn't — go untested. The regression ships.

This is not a discipline problem. It is a tooling problem. The cost of writing good eval cases is high, and the developer who changed the prompt — the person most qualified to know what it might break — is also the most likely to be blind to the risks.

### The Specific Gaps Probegen Fills

**Gap 1: Change-coupled coverage.** No existing tool analyzes what a specific diff is likely to break and generates eval cases for that specific change. Tools like LangSmith, Braintrust, and Promptfoo manage and run evals. They do not generate coverage in response to changes.

**Gap 2: Guardrail change testing.** Changes to LLM judges, output classifiers, validators, and safety filters are at least as behaviorally significant as prompt changes, and are rarely tested with the same rigor. A loosened judge rubric means your evals look better while your agent gets worse.

**Gap 3: Contextually grounded probes.** Generic probe generation produces generic probes. Probes that reflect the actual product, the actual users, the actual vocabulary, and the actual failure history of the system are categorically more valuable.

**Gap 4: Real interaction grounding.** Production traces and real user interactions encode tone, vocabulary, and edge case distributions that no developer-authored eval dataset fully captures.

---

## 3. Why This Approach

### Why Claude Agent SDK as the Harness

Probegen uses the Claude Agent SDK (Python) as its primary orchestration mechanism for the reasoning-intensive pipeline stages.

The Agent SDK gives Claude Code's full agent loop — codebase traversal, tool execution, MCP integration, hooks, session management — as a programmatically controllable Python library. This is the correct harness for probegen because:

- **Codebase variance is the hardest problem.** Users store prompts in Python constants, YAML files, markdown, Jinja templates, and environment variables. A custom traversal layer would need to anticipate every pattern. The Agent SDK encounters the actual codebase and reasons about what it finds.
- **Structured message streaming.** `AssistantMessage`, `ResultMessage`, and `UserMessage` events allow probegen to intercept outputs, validate intermediate results, and handle errors without stdout parsing.
- **Hooks for Stage 4 safety.** Tool call hooks allow probegen to gate actual platform writes behind validation of probe structure before execution.
- **Cost control per stage.** `max_budget_usd` prevents runaway costs in CI.
- **MCP first-class.** Platform integrations via MCP are native to the SDK, not shell-invoked.

The CLI (`--print` flag) is for one-shot scripting. The Agent SDK is for products built on top of the agent loop. Probegen is the latter.

### Why Phased Stages With JSON Handoffs

The pipeline runs as four discrete stages, each a separate Agent SDK invocation, with structured JSON artifacts as the handoff between them.

**Not session-continuous between stages.** Cross-stage session continuity would carry the codebase traversal context from Stage 1 into Stage 2's eval retrieval reasoning. That context is valuable for Stage 1's intent inference but pollutes Stage 2's coverage analysis. Each stage receives exactly the fields it needs from prior stages, extracted from the JSON artifact. Stages are independently retryable.

**Not a single long-running agent.** A single agent session spanning detection through generation accumulates context that degrades reasoning quality (context rot) and makes failures hard to isolate and retry.

**JSON handoffs are precise.** Prose report handoffs are verbose, ambiguous, and cause downstream stages to anchor on the report's framing. Structured JSON passes conclusions, not reasoning traces.

### Why Non-Blocking CI

Probegen does not gate merges. It runs as a parallel job. The rationale:

- Eval generation is a review aid, not a correctness gate. Blocking CI on probe generation would be wrong in the same way that blocking CI on a linter's suggestions would be wrong.
- Probe generation has variable latency. Agent SDK sessions involving codebase traversal and external MCP calls are not fast enough to be on the critical path.
- Trust requires low friction. If probegen ever slows a merge, it will be disabled.

### Why a Hard Approval Gate

Probes are not written to eval platforms until a developer explicitly approves them. The hard gate (not soft) is correct because:

- Soft gates (write but mark pending) pollute eval datasets and create false coverage signals. Teams look at dataset size and feel covered.
- Unapproved probes in a platform accumulate into a backlog nobody processes.
- Eval suite quality depends on intentional authorship. Probegen generates candidates; developers own what goes in.

---

## 4. System Architecture Overview

```
Pull Request (GitHub)
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  GitHub Actions — parallel, non-blocking            │
│                                                     │
│  Stage 1: Change Detection + Intent Analysis        │
│  [Claude Agent SDK] [get_behavior_diff tool]        │
│           │                                         │
│           ▼ BehaviorChangeManifest.json             │
│                    │                                │
│              [GATE: any changes?]                   │
│              NO → stop, no comment                  │
│              YES ↓                                  │
│                                                     │
│  Stage 2: Coverage Gap Analysis                     │
│  [Claude Agent SDK] [MCP: eval platforms]           │
│  [embed_batch tool] [find_similar tool]             │
│           │                                         │
│           ▼ CoverageGapManifest.json                │
│                                                     │
│  Stage 3: Probe Generation + Proposal               │
│  [Claude Agent SDK] [Context Pack]                  │
│           │                                         │
│           ▼ ProbeProposal.json + PR Comment         │
└─────────────────────────────────────────────────────┘
        │
        │  [GATE: developer reviews PR comment]
        │  Developer applies label: probegen:approve
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  Stage 4: Platform Write + Auto-Run                 │
│  Triggered: PR merge + probegen:approve label       │
│  [write_probes.py — direct platform SDK calls]      │
│  [auto-run scoped to new probes]                    │
│  [post results to merged PR]                        │
└─────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| Claude Agent SDK (Stages 1–3) | Reasoning, codebase traversal, MCP orchestration, probe generation |
| `get_behavior_diff` tool | Deterministic: git diff + PR metadata → structured change data |
| `embed_batch` tool | Deterministic: batch embed eval inputs, cache results |
| `find_similar` tool | Deterministic: cosine similarity, classify as duplicate/boundary/related/novel |
| MCP servers (Stage 2) | Read access to eval platforms: LangSmith, Braintrust, Arize Phoenix, Promptfoo |
| `write_probes.py` (Stage 4) | Deterministic: approved probes → platform SDK write calls. No agent involved. |
| Context Pack | Static files: product context, user profiles, interaction patterns, good/bad examples, traces |

---

## 5. Context Pack

The Context Pack is the primary driver of probe specificity and quality. It is the set of contextual documents probegen injects into Stage 1 and Stage 3 to ground the agent's understanding of the product, its users, and what good and bad behavior looks like.

Without a Context Pack, probegen generates probes that are technically correct but generically framed. With a Context Pack, probes reflect the actual product, the actual users, the actual vocabulary, and the actual failure history.

### Context Pack Sources

**1. Product documentation (`context/product.md`)**  
What the product does, who it is for, what problems it solves, what the agent's role is within the product. This determines the domain, register, and stakes of the probes.

**2. User profiles (`context/users.md`)**  
Who the users are: their technical sophistication, their goals, their common frustrations, their vocabulary. A financial analyst asks questions differently than a customer support agent.

**3. Interaction patterns (`context/interactions.md`)**  
Common flows, user stories, expected interaction sequences. This is particularly important for multi-turn probe generation. What does a typical session look like? What does a session that goes wrong look like?

**4. Good examples (`context/good_examples.md`)**  
Examples of excellent agent behavior: the right tone, the right level of detail, the right tool choices, the right citations. These teach the probe generator what "pass" looks like in rubric terms.

**5. Bad examples / known failure modes (`context/bad_examples.md`)**  
Known failures, historical regressions, bugs that reached production, edge cases that have previously broken the agent. These are the highest-value seeds for probe generation. A known real failure mode is a guaranteed-realistic probe.

**6. Production traces (`context/traces/`)**  
Real user interactions sampled from production logs. These encode tone, vocabulary, real user intent, and the actual distribution of edge cases. Traces are distinct from evals: evals encode what the team thought was important; traces encode what users actually do.

> **Important — PII and sanitisation responsibility:** Production traces may contain personally identifiable information or sensitive user data. Probegen does not sanitise traces. The user is solely responsible for ensuring that any trace data provided in the Context Pack has been appropriately anonymised before being committed to the repository or supplied to probegen. This requirement must be documented prominently in onboarding.

### What Traces Add Beyond Evals

| Signal | Source | Value |
|---|---|---|
| Tone and register | Traces | How users actually phrase requests — terse, colloquial, ambiguous |
| Domain vocabulary | Traces | Abbreviations, product-specific terms, real user language |
| Interaction patterns | Traces | What follow-ups are common, what sequences recur |
| Multi-turn structure | Traces | What real conversation histories look like for this agent |
| Real failure seeds | Traces | What actually trips users up in production |
| Coverage awareness | Evals | What the team decided to test |
| Good/bad definitions | Evals | What pass and fail look like in structured rubric terms |
| Boundary awareness | Evals | Where the existing test boundaries sit |

### Context Pack Configuration

```yaml
# probegen.yaml
context:
  product: "context/product.md"
  users: "context/users.md"
  interactions: "context/interactions.md"
  good_examples: "context/good_examples.md"
  bad_examples: "context/bad_examples.md"
  traces_dir: "context/traces/"        # optional, .txt or .json files
  trace_max_samples: 20                # cap how many traces are injected per stage run
```

### Context Pack Injection Points

- **Stage 1:** product.md + bad_examples.md injected into the intent analysis prompt. Grounds the "what could this change break" reasoning in real failure history.
- **Stage 3:** full Context Pack injected into the probe generation prompt. This is the primary quality driver. The generator sees the product, the users, the interaction patterns, good/bad examples, and trace samples before generating a single probe.

### `probegen init` Context Pack Prompting

During `probegen init`, if no context directory exists, the user is asked:

```
No context/ directory found. Probegen works significantly better with product context.
Would you like to create a context pack? (recommended)

We'll create stub files for:
  context/product.md         — What your product does and who uses it
  context/users.md           — User profiles and personas
  context/interactions.md    — Common flows and interaction patterns
  context/good_examples.md   — Examples of correct agent behavior
  context/bad_examples.md    — Known failure modes and edge cases
  context/traces/            — (optional) Anonymised production trace samples

[Y/n]
```

Stub files are pre-populated with section headers and brief instructions. The user fills them in. Probegen functions without a Context Pack but emits a warning on every run indicating that probe quality will be significantly reduced.

---

## 6. Pipeline: Stage 1 — Change Detection and Intent Analysis

### Purpose

Stage 1 answers: **what changed, and what was the developer trying to accomplish?**

It detects all behavior-defining and guardrail artifact changes in the PR, reasons about intent from the diff (using the PR description as a secondary signal), and produces the `BehaviorChangeManifest` that gates all downstream stages.

### Trigger

Every PR open, synchronize, or reopen event.

### Agent SDK Invocation

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for message in query(
    prompt=render_stage1_prompt(context),
    options=ClaudeAgentOptions(
        allowed_tools=["Bash", "Read", "Glob"],
        mcp_servers=[],          # no MCP in Stage 1
        max_turns=40,
        max_budget_usd=0.50,
        cwd=repo_root,
    )
):
    handle_message(message)
```

### Tools Available

**`Bash`, `Read`, `Glob`** — the agent's primary discovery tools. Stage 1 uses these to:
- Fetch file content for changed files not pre-loaded: `git show origin/{base}:{path}`, `git diff ... -- {path}`
- Understand how a changed artifact is used: which agent class imports it, which API route invokes it
- Inspect Python files for module-level string constants that act as prompts

**Prompt receives:**
- `all_changed_files` — lightweight list of every file changed in the PR (path + change_kind)
- `hint_matched_artifacts` — full before/after content + diff, pre-loaded for files matching configured hint patterns
- `hint_patterns` — the configured patterns, presented to the agent as hints, not filters

The agent starts with pre-loaded hint matches, then reviews `all_changed_files` and fetches any file it judges behaviorally significant. Config patterns guide focus but do not constrain discovery.

> **Important:** Configured hint patterns (`behavior_artifacts.paths` and `python_patterns`) are *discovery hints*, not filters. They serve two purposes:
> 1. **Performance optimization** — files matching patterns have their content pre-loaded, reducing git fetch calls
> 2. **Focus guidance** — help the agent identify common naming conventions
>
> The agent always has full visibility into **all changed files** in the PR and can inspect any of them using Read, Bash, and Glob tools. If a behavioral change exists in a file that doesn't match any pattern, the agent will detect it. The patterns are an optimization for the common case, not a gate on discovery.

### Stage 1 Prompt Design

```
SYSTEM:
You are a behavioral change analyst for LLM-based agent systems.

PRODUCT CONTEXT:
{product_context}

KNOWN FAILURE MODES:
{bad_examples}

PR METADATA:
{pr_metadata_json}

ALL CHANGED FILES IN THIS PR:
{all_changed_files_json}
(All files modified, added, or deleted in this PR.)

HINT-PATTERN MATCHES — PRE-LOADED:
{hint_matched_artifacts_json}
(Files matching configured hint patterns — pre-loaded with before/after content and diffs.)

CONFIGURED HINT PATTERNS:
{hint_patterns_json}
(Patterns your team configured as hints — not a complete list. Behavioral changes may
appear in files that don't match any pattern.)

PROCESS:
1. Start with pre-loaded hint-pattern matches. Analyze each for behavioral significance.
2. Review ALL CHANGED FILES. For any file not pre-loaded that could be behaviorally
   significant, fetch and inspect it:
     - Read the file:       Read tool on the current path
     - Get before content:  Bash: git show origin/{base_branch}:<path>
     - Get the diff:        Bash: git diff --unified=5 origin/{base_branch}...HEAD -- <path>
3. Behavioral artifacts include (not limited to): system prompts, tool descriptions,
   LLM judges, output validators, guardrails, retrieval instructions, retry policies,
   fallback prompts — any file whose change alters what the LLM agent does or decides.
4. For Python files: look for module-level string constants that contain prompt-like content.
5. Classify each behavioral artifact you discover. For each:
   a. Infer the intended behavioral change from the diff.
   b. Compare against PR description. Flag contradictions.
   c. Identify unintended risks: what could go wrong that the developer may not have
      considered.
   d. For guardrail artifacts, reason in both directions:
      - False negative risk: what should still be caught that might not be
      - False positive risk: what should still pass through that might be blocked
6. Detect compound changes: BOTH a behavior-defining artifact AND a guardrail artifact
   changed in the same PR. Flag these as highest-risk.
7. Traverse the codebase to identify affected components for each artifact.
8. Output BehaviorChangeManifest JSON only. No prose.

QUALITY REQUIREMENT:
"The agent may behave differently in edge cases" is not acceptable output.
"The citation rule is not scoped to register; conversational queries may now receive
citations where they should not" is acceptable output.
```

### Output: `BehaviorChangeManifest`

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "pr_number": 142,
  "commit_sha": "abc123",
  "timestamp": "2026-03-14T10:00:00Z",
  "has_changes": true,
  "overall_risk": "medium",
  "pr_intent_summary": "Adding citation requirement for factual queries in CitationAgent",
  "pr_description_alignment": "confirmed",
  "compound_change_detected": false,
  "changes": [
    {
      "artifact_path": "prompts/citation_agent/system_prompt.md",
      "artifact_type": "system_prompt",
      "artifact_class": "behavior_defining",
      "change_type": "modification",
      "inferred_intent": "Require citations on factual queries to improve answer verifiability",
      "pr_description_alignment": "confirmed",
      "unintended_risk_flags": [
        "Citation rule not scoped to query register — conversational queries may trigger unwanted citations",
        "No exception defined for cases where source material is unavailable or ambiguous",
        "Rule may interact with existing tone instructions in unexpected ways"
      ],
      "affected_components": [
        "agents/citation_agent.py::CitationAgent",
        "api/routes/search.py::search_handler"
      ],
      "false_negative_risks": [],
      "false_positive_risks": [],
      "change_summary": "Added: 'Always cite sources when answering factual questions'",
      "before_hash": "sha256:...",
      "after_hash": "sha256:..."
    }
  ],
  "compound_changes": []
}
```

### Gate Logic

After Stage 1 completes:

```python
manifest = load_json(".probegen/stage1.json")
if not manifest["has_changes"]:
    # Post a minimal "no behavioral changes detected" comment, then exit 0.
    # Downstream stages do not run.
    sys.exit(0)
```

If `has_changes` is false, the workflow posts a minimal no-changes comment and stops. This is the path for the majority of PRs. The comment explains that no behavioral artifacts were detected and points to hint pattern configuration if the user believes a change was missed.

---

## 7. Pipeline: Stage 2 — Coverage Gap Analysis

### Purpose

Stage 2 answers: **what does the existing eval coverage look like for the changed behavior, and where are the gaps?**

When relevant eval cases exist, Stage 2 performs coverage-aware comparison against the existing corpus. When no usable eval corpus exists yet, Stage 2 switches to bootstrap mode and derives baseline gaps directly from the diff, the inferred behavioral risks, and the available business context. In both cases, the output is a `CoverageGapManifest`.

### Trigger

Stage 1 completed with `has_changes: true`.

### Agent SDK Invocation

```python
async for message in query(
    prompt=render_stage2_prompt(manifest=stage1_manifest),
    options=ClaudeAgentOptions(
        allowed_tools=["Bash"],
        mcp_servers=get_configured_mcp_servers(),  # LangSmith, Braintrust, Arize, etc.
        max_turns=40,
        max_budget_usd=0.75,
        cwd=repo_root,
    )
):
    handle_message(message)
```

### Input

The `BehaviorChangeManifest` from Stage 1, with raw diffs stripped. Stage 2 receives:
- `artifact_path`, `artifact_type`, `artifact_class`, `inferred_intent`
- `unintended_risk_flags`
- `false_negative_risks`, `false_positive_risks` (for guardrail artifacts)
- `affected_components`
- `overall_risk`, `compound_change_detected`

Raw diffs are not passed to Stage 2. They were needed for Stage 1's reasoning; they are noise for Stage 2.

### Tools Available

**MCP servers** — Claude Code natively calls configured MCP tools to retrieve eval datasets:

| Platform | MCP Endpoint | Read Capability |
|---|---|---|
| LangSmith | `langsmith-mcp-server` (stdio) | `fetch_datasets`, `fetch_examples`, `read_example` |
| Braintrust | `https://api.braintrust.dev/mcp` (HTTP) | `sql_query` (BTQL), `list_recent_objects`, `infer_schema` |
| Arize Phoenix | `npx @arizeai/phoenix-mcp` (stdio) | `list_datasets`, `get_dataset`, `list_experiments` |
| Promptfoo | Direct file read via `Bash`/`Read` | Parse `promptfooconfig.yaml` test cases |

The agent uses MCP tools to retrieve eval cases for the datasets mapped to the changed artifacts in `probegen.yaml`. If no mapping exists for an artifact, it posts a warning (see Section 15: Missing Mapping Handling). If a mapping exists but the dataset is empty, or no datasets exist at all, Stage 2 records bootstrap coverage instead of failing.

**`embed_batch` (probegen tool, called via Bash)**  
Takes a list of normalised input strings, returns embeddings using `text-embedding-3-small` (default) or configured alternative. Uses SQLite cache at `.probegen/embedding_cache.db`. Returns embeddings; never re-embeds inputs with a cache hit.

```bash
probegen embed-batch --inputs inputs.json --output embeddings.json
```

**`find_similar` (probegen tool, called via Bash)**  
Takes a probe candidate input and the corpus embeddings, returns similarity classification for each corpus item.

```bash
probegen find-similar --candidate candidate.json --corpus embeddings.json --output similarity.json
```

Similarity thresholds (configurable in `probegen.yaml`):

| Score | Classification | Action |
|---|---|---|
| ≥ 0.88 | `duplicate` | Mark as covered; discard as probe candidate |
| 0.72–0.87 | `boundary` | Flag as boundary shift opportunity |
| 0.50–0.71 | `related` | Retain as context; candidate for boundary probe |
| < 0.50 | `novel` | Coverage gap; strong probe candidate |

### Input Normalisation

All eval inputs — regardless of platform schema — are normalised to a flat string before embedding. The normalisation tool handles all formats:

```
string  → as-is
list    → "ROLE: content\nROLE: content\n..." (conversation format)
dict    → priority lookup for keys: query, input, question, message, user_message, prompt
         fallback: JSON serialise all fields
```

Multi-turn conversation inputs are detected when the input is a list of role/content objects. When detected, the artifact mapping is automatically flagged `conversational: true` for Stage 3.

### Dataset Mapping Resolution

For each changed artifact, Stage 2 resolves which eval dataset to query in this order:

1. **Explicit mapping** in `probegen.yaml` — used directly
2. **Convention matching** — dataset name contains the artifact's parent directory name, or dataset tags include the agent name
3. **Interactive prompt** — if no mapping resolves, Stage 2 records the unmapped artifact in `CoverageGapManifest.unmapped_artifacts`. A warning is posted in the PR comment with instructions to add the mapping.

### Stage 2 Prompt Design

```
SYSTEM:
You are a coverage gap analyst for LLM-based agent evaluation suites. Your task is to 
retrieve existing eval coverage for a set of changed artifacts and identify where coverage 
is missing relative to the predicted behavioral impacts.

PROCESS:
1. For each changed artifact in the manifest, retrieve existing eval cases using the 
   available MCP tools for the configured eval platform.
2. For each retrieved case, call embed_batch to compute embeddings (the tool handles 
   caching automatically).
3. For each risk flag and predicted impact in the manifest, call find_similar to 
   determine whether existing coverage addresses it.
4. Classify each risk flag as: covered | boundary_shift | uncovered
5. For guardrail artifacts, analyze coverage in both directions:
   - Are there existing cases testing things the guardrail should catch?
   - Are there existing cases testing things the guardrail should allow through?
6. If no relevant eval cases exist, switch to bootstrap mode:
   - mark `coverage_summary.mode` as `bootstrap`
   - mark `coverage_summary.corpus_status` as `empty` or `unavailable`
   - explain the reason in `coverage_summary.bootstrap_reason`
   - emit baseline gaps with empty `nearest_existing_cases`
7. Identify the top-priority gaps: uncovered risk flags ranked by severity.
8. Output the CoverageGapManifest JSON.

IMPORTANT: Do not generate probes in this stage. Identify gaps only.
```

### Output: `CoverageGapManifest`

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "stage1_run_id": "uuid",
  "timestamp": "2026-03-14T10:01:00Z",
  "unmapped_artifacts": [],
  "coverage_summary": {
    "total_relevant_cases": 34,
    "cases_covering_changed_behavior": 12,
    "coverage_ratio": 0.35,
    "platform": "langsmith",
    "dataset": "citation-agent-evals",
    "mode": "coverage_aware",
    "corpus_status": "available",
    "bootstrap_reason": null
  },
  "gaps": [
    {
      "gap_id": "gap_001",
      "artifact_path": "prompts/citation_agent/system_prompt.md",
      "gap_type": "uncovered",
      "related_risk_flag": "Citation rule not scoped to register — conversational queries may trigger unwanted citations",
      "description": "No existing case tests citation behavior in conversational register",
      "nearest_existing_cases": [
        {
          "case_id": "case_a3f2",
          "input_normalized": "What is the Paris Agreement?",
          "similarity": 0.71,
          "classification": "related"
        }
      ],
      "priority": "high",
      "guardrail_direction": null,
      "is_conversational": false
    }
  ]
}
```

---

## 8. Pipeline: Stage 3 — Probe Generation and Proposal

### Purpose

Stage 3 generates targeted eval probes for each identified coverage gap, ranks them, filters for quality and diversity, and produces a PR comment and structured proposal artifact for developer review.

### Trigger

Stage 2 completed and `CoverageGapManifest.gaps` is non-empty.

### Agent SDK Invocation

```python
async for message in query(
    prompt=render_stage3_prompt(
        gaps=stage2_manifest,
        intent=stage1_manifest_stripped,
        context_pack=load_context_pack(),
    ),
    options=ClaudeAgentOptions(
        allowed_tools=["Bash"],   # only for find_similar on generated candidates
        mcp_servers=[],           # no MCP in Stage 3
        max_turns=25,
        max_budget_usd=1.00,
        cwd=repo_root,
    )
):
    handle_message(message)
```

### Context Injection (Stage 3)

Stage 3 receives a curated context package. It deliberately does not receive the full accumulated context of prior stages — only the fields needed for generation:

```
FROM STAGE 1 (stripped):
  - inferred_intent per artifact
  - unintended_risk_flags per artifact
  - affected_components per artifact
  - overall_risk, compound_change_detected

FROM STAGE 2:
  - coverage_summary (including whether Stage 2 is coverage-aware or bootstrap)
  - gaps array (full)
  - nearest_existing_cases per gap (input + classification)

FROM CONTEXT PACK (full injection):
  - product.md
  - users.md
  - interactions.md
  - good_examples.md
  - bad_examples.md
  - trace samples (up to trace_max_samples, randomly sampled from traces_dir)
```

The trace samples are the mechanism by which real user tone and vocabulary enter the probe generation. The agent sees real interactions before generating synthetic ones.

### Stage 3 Prompt Design

```
SYSTEM:
You are a behavioral probe generator for LLM-based agent systems. Your task is to generate 
targeted evaluation test cases that test the specific behavioral consequences of a recent 
change to an agent's behavior-defining or guardrail artifacts.

PRODUCT CONTEXT:
{product_context}

USER PROFILES:
{users_context}

INTERACTION PATTERNS:
{interactions_context}

WHAT GOOD LOOKS LIKE:
{good_examples}

KNOWN FAILURE MODES:
{bad_examples}

REAL USER INTERACTION SAMPLES (use these to calibrate tone, vocabulary, and realism):
{trace_samples}

QUALITY CRITERIA — every probe must satisfy ALL of the following:
1. SPECIFICITY: Tests exactly one named consequence of the diff. No generic probes.
2. TESTABILITY: Expected behavior is unambiguous — a human can determine pass/fail 
   without subjective judgment, OR a precise LLM-as-judge rubric is provided.
3. NOVELTY: Not semantically equivalent to any existing case in the nearest_existing_cases 
   provided. Must test behavior the existing suite does not cover.
4. REALISM: Input looks like something a real user of this product would actually send. 
   Use the tone, vocabulary, and patterns from the trace samples and interaction patterns.
5. BOUNDARY AWARENESS: If the probe is near an existing case (similarity 0.72–0.87), 
   classify it as boundary_probe and reference the nearest existing case. This is 
   intentional — boundary probes test whether a behavioral boundary has shifted.

BOOTSTRAP MODE:
If `coverage_summary.mode` is `bootstrap`, there is no usable eval corpus for comparison.
Generate plausible starter evals from the diff, system prompt, guardrails, product context,
user profiles, interaction patterns, good/bad examples, and traces. Empty
`nearest_existing_cases` are expected in this mode. Do not invent comparisons to missing
evals, and prefer probe types other than `boundary_probe` unless a real nearest case exists.

ANTI-PATTERNS — never produce:
- Generic probes that would apply to any agent ("does the agent respond helpfully?")
- Probes whose expected behavior is "it depends"
- Probes testing capabilities not touched by this diff
- More than two probes addressing the same gap
- Inputs that sound synthetic or developer-authored rather than user-natural

MULTI-TURN PROBES (for conversational: true gaps):
When generating probes for conversational agents, generate full conversation histories 
not single-turn inputs. Use the trace samples and interaction patterns to understand 
the expected turn structure and length. The conversation history leading up to the 
final turn is the context that makes the probe realistic.

GUARDRAIL PROBES:
For guardrail gaps, generate probes in both directions:
- should_catch: an input/output that the guardrail should still flag (false negative test)
- should_pass: an input/output that the guardrail should still allow (false positive test)

Generate up to 20 candidate probes internally, then select the best {max_probes_surfaced} 
based on quality criteria. Apply a diversity filter: no more than 2 probes per gap_id.

Output ProbeProposal JSON only. No prose.
```

### Probe Types

| Type | Description | When Generated |
|---|---|---|
| `regression_guard` | Tests that a behavior that should still work still works | Whenever an existing behavior is at risk |
| `expected_improvement` | Tests that the intended improvement was achieved | For all intended changes |
| `boundary_probe` | Near-similar to existing case; tests whether a behavioral boundary shifted | When similarity is 0.72–0.87 to an existing case |
| `edge_case` | Scenarios where the new instruction is ambiguous or underspecified | When risk flags include ambiguity |
| `overcorrection_probe` | Tests that the intended improvement didn't overshoot | When change direction is restrictive |
| `tool_selection_probe` | Tests correct tool routing after instruction changes | For planner/tool-description changes |
| `ambiguity_probe` | Tests behavior when input is genuinely ambiguous under the new rule | When new rule has underspecified scope |
| `judge_calibration_probe` | An agent output to be evaluated; tests the guardrail itself | For guardrail artifact changes only |

### Probe Count Computation

```python
def compute_probe_count(manifest: BehaviorChangeManifest) -> int:
    base = len(manifest.changes) * 3
    risk_multiplier = {"low": 0.6, "medium": 1.0, "high": 1.4}[manifest.overall_risk]
    if manifest.compound_change_detected:
        risk_multiplier *= 1.3
    raw = int(base * risk_multiplier)
    return max(3, min(raw, 12))   # floor 3, ceiling 12
```

12 probes maximum surfaced to developer. A PR comment with more than 12 probes will not be reviewed.

### Diversity Filter

After generation, before surfacing:
1. Run `find_similar` on all candidate probes against the existing eval corpus — discard any scoring ≥ 0.88 (duplicate)
2. Re-classify 0.72–0.87 scorers as `boundary_probe`
3. Apply diversity cap: retain maximum 2 probes per `gap_id`
4. Rank remaining by composite score (see Ranking below)
5. Take top `max_probes_surfaced`

If Stage 2 is in bootstrap mode and there is no existing eval corpus, skip the corpus duplicate-filter step and rank generated probes only by the quality criteria plus gap priority.

### Probe Ranking

```python
def score_probe(probe: ProbeCase, gaps: list[Gap]) -> float:
    WEIGHTS = {
        "specificity":    0.30,
        "testability":    0.25,
        "novelty":        0.20,
        "realism":        0.15,
        "risk_alignment": 0.10,
    }
    novelty = 1.0 - (probe.nearest_existing_similarity or 0.0)
    gap = next((g for g in gaps if g.gap_id == probe.gap_id), None)
    risk_alignment = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
        gap.priority if gap else "medium", 0.6
    )
    return (
        WEIGHTS["specificity"]    * probe.specificity_confidence +
        WEIGHTS["testability"]    * probe.testability_confidence +
        WEIGHTS["novelty"]        * novelty +
        WEIGHTS["realism"]        * probe.realism_confidence +
        WEIGHTS["risk_alignment"] * risk_alignment
    )
```

### Output: `ProbeProposal`

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "stage1_run_id": "uuid",
  "stage2_run_id": "uuid",
  "timestamp": "2026-03-14T10:02:00Z",
  "pr_number": 142,
  "commit_sha": "abc123",
  "probe_count": 7,
  "probes": [
    {
      "probe_id": "probe_001",
      "gap_id": "gap_001",
      "probe_type": "boundary_probe",
      "is_conversational": false,
      "input": "So what do you think about climate policy generally?",
      "input_format": "string",
      "expected_behavior": "Agent responds conversationally without inserting a citation or source reference",
      "expected_behavior_type": "llm_rubric",
      "rubric": "The response does NOT include a citation, footnote, or 'according to [source]' phrasing. It responds naturally and conversationally to an opinion-style question.",
      "probe_rationale": "Tests the boundary introduced by the new citation rule: conversational/opinion queries should not trigger citations, but the rule is not scoped to exclude them. This is near case_a3f2 (similarity 0.74) which tested factual citation presence — this probe tests the opposite direction at the register boundary.",
      "nearest_existing_case_id": "case_a3f2",
      "nearest_existing_similarity": 0.74,
      "related_risk_flag": "Citation rule not scoped to register — conversational queries may trigger unwanted citations",
      "specificity_confidence": 0.91,
      "testability_confidence": 0.88,
      "realism_confidence": 0.85,
      "approved": false
    }
  ],
  "export_formats": {
    "promptfoo": ".probegen/runs/abc123/probes.yaml",
    "deepeval": null,
    "raw_json": ".probegen/runs/abc123/probes.json"
  }
}
```

### PR Comment Format

```markdown
## 🔍 Probegen: Behavioral Impact Detected

**Artifact:** `prompts/citation_agent/system_prompt.md`  
**Risk level:** Medium  
**Primary change:** Citation requirement added for factual queries  
⚠️ **Note:** PR description matches inferred intent.

### Behavioral Impact Summary
- ✅ Intended: Agent cites sources on factual queries
- ⚠️ Regression risk: Citation rule not scoped to register
- ⚠️ Edge case: Behavior undefined when sources are ambiguous

### Proposed Probes (7)

| # | Type | Input (truncated) | Tests |
|---|---|---|---|
| 1 | `boundary_probe` | "So what do you think about climate..." | No citation in conversational |
| 2 | `expected_improvement` | "What year was the Paris Agreement signed?" | Citation present on factual Q |
| 3 | `overcorrection_probe` | "Can you explain how photosynthesis works?" | Citation not forced on explanatory |
| 4 | `edge_case` | "Scientists say X causes Y. Is that true?" | Behavior when user cites a source |
| 5 | `regression_guard` | "Help me write an email to my manager" | No citation in non-factual task |
| 6 | `ambiguity_probe` | "What's the general consensus on..." | Partial evidence scenario |
| 7 | `judge_calibration_probe` | [agent output with spurious citation] | Judge still catches over-citation |

**To approve all probes:** Add label `probegen:approve` to this PR.  
**Full proposal + rationale:** `.probegen/runs/abc123/ProbeProposal.json`  
**Promptfoo export:** `.probegen/runs/abc123/probes.yaml`
```

---

## 9. Pipeline: Stage 4 — Platform Write and Auto-Run

### Purpose

After developer approval, write approved probes to the eval platform and trigger a scoped eval run to validate whether the predicted behavioral impacts occurred.

### Trigger

Two conditions must both be true:
1. PR is merged to the base branch
2. Label `probegen:approve` was applied to the PR at any point before merge

### Important: No Agent SDK in Stage 4

Stage 4 is entirely deterministic. The reasoning is done. Approved probes are known. The write is mechanical. Stage 4 is a Python script (`write_probes.py`) called directly in the GitHub Action — not an Agent SDK session.

Using the Agent SDK for a deterministic write operation adds unnecessary cost, latency variance, and failure surface. Stage 4 must be fast, reliable, and auditable.

### Write Implementation Per Platform

**LangSmith** (direct SDK, MCP write not supported):
```python
from langsmith import Client

def write_to_langsmith(probes, dataset_name, api_key):
    client = Client(api_key=api_key)
    dataset = client.read_dataset(dataset_name=dataset_name)
    client.create_examples(
        inputs=[{"query": p.input} if isinstance(p.input, str) else p.input
                for p in probes],
        outputs=[{"expected_behavior": p.expected_behavior} for p in probes],
        metadata=[{
            "probe_type": p.probe_type,
            "rationale": p.probe_rationale,
            "rubric": p.rubric,
            "generated_by": "probegen",
            "source_pr": os.environ["PR_NUMBER"],
            "source_commit": os.environ["COMMIT_SHA"],
            "probe_id": p.probe_id,
        } for p in probes],
        dataset_id=dataset.id,
    )
```

**Braintrust** (direct SDK, MCP write not confirmed):
```python
import braintrust

def write_to_braintrust(probes, project, dataset_name, api_key):
    dataset = braintrust.init_dataset(
        project=project, name=dataset_name, api_key=api_key
    )
    dataset.insert([{
        "input": p.input,
        "expected": p.expected_behavior,
        "metadata": {
            "probe_type": p.probe_type,
            "rationale": p.probe_rationale,
            "rubric": p.rubric,
            "generated_by": "probegen",
            "probe_id": p.probe_id,
        },
        "tags": [p.probe_type, "probegen"],
    } for p in probes])
```

**Arize Phoenix** (MCP write confirmed, but direct SDK used for reliability in CI):
```python
import phoenix as px

def write_to_phoenix(probes, dataset_name):
    client = px.Client()
    client.upload_dataset(
        dataset_name=dataset_name,
        dataframe=probes_to_dataframe(probes),
        input_keys=["input"],
        output_keys=["expected_behavior"],
        metadata_keys=["probe_type", "rationale", "probe_id"],
    )
```

**Promptfoo** (file append):
```python
def write_to_promptfoo(probes, test_file):
    with open(test_file) as f:
        config = yaml.safe_load(f)
    config["tests"] = config.get("tests", []) + [
        probe_to_promptfoo_test(p) for p in probes
    ]
    with open(test_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    # Creates a follow-up commit or PR; see CI config
```

### Auto-Run

After write completes, trigger an eval run scoped to the newly added probes. This is platform-specific:

- **LangSmith:** trigger dataset run via REST API filtered by `generated_by: probegen` and `source_commit: {sha}`
- **Braintrust:** `braintrust eval` CLI with tag filter `probegen`
- **Arize Phoenix:** `px.run_experiment()` scoped to the new dataset entries
- **Promptfoo:** `promptfoo eval --filter-description "probegen"` 

Auto-run is configurable and can be disabled:
```yaml
auto_run:
  enabled: true
  fail_on: regression_guard   # fail the Stage 4 job if any regression_guard probe fails
  notify: pr_comment          # post results back to the merged PR
```

### Results Post-Back

Stage 4 posts a follow-up comment on the merged PR (GitHub allows comments on closed/merged PRs):

```markdown
## ✅ Probegen: Probes Added + Results

**7 probes written to:** `citation-agent-evals` (LangSmith)  
**Auto-run completed:** 5 passed, 2 failed

### Failures
| Probe | Type | Failure |
|---|---|---|
| probe_001 | `boundary_probe` | Agent inserted citation in conversational query — regression confirmed |
| probe_006 | `ambiguity_probe` | Agent hallucinated source when evidence was unavailable |

**Regression detected.** Consider reverting or scoping the citation rule to factual query types only.
```

---

## 10. Approval Mechanism

### v1 Mechanism: GitHub Label

The developer applies the label `probegen:approve` to the PR. This is the sole v1 approval mechanism.

**Why label over other options:**
- Requires no additional infrastructure beyond a second workflow file
- Native to GitHub PR review flow
- Asynchronous — developer approves when ready, not when the pipeline completes
- Does not require a GitHub App or webhook server
- Works naturally with PR review processes (team lead reviews probes, applies label)

**Partial approval:** Not supported in v1. The label is binary. The developer reviews all proposed probes in the PR comment, mentally dismisses any they disagree with, and applies the label when satisfied with the remaining set. Unapproved probes (those the developer chose not to include) are discarded — the `ProbeProposal.json` artifact is retained for reference but no further action is taken on unapproved probes.

**Label configuration:**
```yaml
# probegen.yaml
approval:
  label: "probegen:approve"    # customisable
```

### Workflow Trigger for Stage 4

```yaml
on:
  pull_request:
    types: [closed]
    
jobs:
  probegen-write:
    if: |
      github.event.pull_request.merged == true &&
      contains(github.event.pull_request.labels.*.name, 'probegen:approve')
    runs-on: ubuntu-latest
    steps:
      - name: Retrieve probe proposal
        uses: actions/download-artifact@v4
        with:
          name: probegen-${{ github.event.pull_request.number }}
          
      - name: Write approved probes
        run: python -m probegen.write_probes --proposal ProbeProposal.json
        env:
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          COMMIT_SHA: ${{ github.event.pull_request.merge_commit_sha }}
```

---

## 11. Eval Platform Integrations

### Integration Matrix

| Platform | MCP Read | Write Method | Auto-Run | Notes |
|---|---|---|---|---|
| LangSmith | ✅ stdio server | Python SDK direct | REST API | MCP write is docs-only; SDK required for writes |
| Braintrust | ✅ HTTP server (`api.braintrust.dev/mcp`) | Python SDK direct | `braintrust eval` CLI | MCP supports read/query; write confirmation pending |
| Arize Phoenix | ✅ npm server (`@arizeai/phoenix-mcp`) | Python SDK direct | `px.run_experiment()` | Both read and write MCP confirmed; SDK used for CI reliability |
| Promptfoo | ❌ File-based | File append | `promptfoo eval` CLI | No API; YAML file read/write |
| Humanloop | ❌ Sunsetted Sept 2025 | N/A | N/A | Removed from scope entirely |

### MCP Configuration Generation

`probegen setup-mcp` generates `.claude/mcp_servers.json` from `probegen.yaml` + available env vars:

```python
def generate_mcp_config(config: ProbegenConfig, env: dict) -> dict:
    servers = {}
    
    if env.get("LANGSMITH_API_KEY") and config.platforms.langsmith:
        servers["langsmith"] = {
            "command": "uvx",
            "args": ["langsmith-mcp-server"],
            "env": {"LANGCHAIN_API_KEY": env["LANGSMITH_API_KEY"]}
        }
    
    if env.get("BRAINTRUST_API_KEY") and config.platforms.braintrust:
        servers["braintrust"] = {
            "type": "http",
            "url": "https://api.braintrust.dev/mcp",
            "headers": {
                "Authorization": f"Bearer {env['BRAINTRUST_API_KEY']}"
            }
        }
    
    if env.get("PHOENIX_API_KEY") and config.platforms.arize_phoenix:
        servers["phoenix"] = {
            "command": "npx",
            "args": ["-y", "@arizeai/phoenix-mcp@latest",
                     "--baseUrl", config.platforms.arize_phoenix.base_url,
                     "--apiKey", env["PHOENIX_API_KEY"]]
        }
    
    return {"mcpServers": servers}
```

Only platforms with a configured API key are included. Missing platforms silently degrade to file export.

### File Export (Fallback and Always-Present)

Regardless of platform integration, every Stage 3 run writes to `.probegen/runs/{commit_sha}/`:

```
.probegen/runs/{commit_sha}/
  ├── BehaviorChangeManifest.json     # Stage 1 output
  ├── CoverageGapManifest.json        # Stage 2 output  
  ├── ProbeProposal.json              # Stage 3 output (all probes, full detail)
  ├── probes.yaml                     # Promptfoo-compatible, ready to use
  ├── summary.md                      # Human-readable full probe list with rationale
  └── metadata.json                   # Run metadata: model, cost, duration, versions
```

These artifacts are uploaded as GitHub Actions artifacts and retained for 90 days (configurable).

---

## 12. GitHub Action and CI Integration

### Full Workflow File

```yaml
# .github/workflows/probegen.yml
name: Probegen

on:
  pull_request:
    types: [opened, synchronize, reopened]
  pull_request_target:
    types: [closed]

permissions:
  contents: read
  pull-requests: write

jobs:
  # ─── STAGES 1–3: Analysis and Proposal ──────────────────────────────────────
  probegen-analyze:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install probegen
          npm install -g @anthropic-ai/claude-code

      - name: Stage 1 — Change Detection
        run: |
          probegen run-stage 1 \
            --pr-number ${{ github.event.pull_request.number }} \
            --base-branch ${{ github.event.pull_request.base.ref }} \
            --output .probegen/stage1.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_EVENT_PATH: ${{ github.event_path }}

      - name: Check gate
        id: gate
        run: |
          has_changes=$(python -c "
          import json
          m = json.load(open('.probegen/stage1.json'))
          print('true' if m.get('has_changes') else 'false')
          ")
          echo "has_changes=$has_changes" >> $GITHUB_OUTPUT

      - name: Stage 2 — Coverage Analysis
        if: steps.gate.outputs.has_changes == 'true'
        run: |
          probegen run-stage 2 \
            --manifest .probegen/stage1.json \
            --output .probegen/stage2.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}

      - name: Stage 3 — Probe Generation
        if: steps.gate.outputs.has_changes == 'true'
        run: |
          probegen run-stage 3 \
            --manifest .probegen/stage1.json \
            --gaps .probegen/stage2.json \
            --output .probegen/stage3.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Post PR comment (no changes)
        if: steps.gate.outputs.has_changes == 'false'
        run: probegen post-comment --no-changes --pr-number ${{ github.event.pull_request.number }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Post PR comment (probes)
        if: steps.gate.outputs.has_changes == 'true'
        run: |
          probegen post-comment \
            --proposal .probegen/stage3.json \
            --pr-number ${{ github.event.pull_request.number }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: probegen-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .probegen/
          retention-days: 90

  # ─── STAGE 4: Write + Auto-Run (post-merge, post-approval) ──────────────────
  probegen-write:
    if: |
      github.event_name == 'pull_request_target' &&
      github.event.pull_request.merged == true &&
      contains(github.event.pull_request.labels.*.name, 'probegen:approve')
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install probegen

      - name: Resolve analysis run
        id: resolve
        run: |
          run_id=$(probegen resolve-run-id \
            --repo ${{ github.repository }} \
            --workflow-id probegen.yml \
            --head-sha ${{ github.event.pull_request.head.sha }})
          echo "run_id=$run_id" >> $GITHUB_OUTPUT
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Download probe proposal
        uses: actions/download-artifact@v4
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ steps.resolve.outputs.run_id }}
          name: probegen-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .probegen/

      - name: Write probes to platform
        run: |
          probegen write-probes \
            --proposal .probegen/stage3.json \
            --config probegen.yaml
        env:
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          COMMIT_SHA: ${{ github.event.pull_request.merge_commit_sha }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_RUN_ID: ${{ github.run_id }}
```

### Non-Blocking Guarantee

The `probegen-analyze` job runs in parallel with all other CI jobs and is never referenced in branch protection rules. It cannot block a merge. It is a review aid.

---

## 13. User Setup and Configuration

### Prerequisites

- Node.js 22+ (for Claude Code CLI, required by Agent SDK)
- Python 3.11+
- GitHub Actions enabled on the repository
- Anthropic API key (required)
- At least one eval platform API key if you want direct platform integration or automatic writeback (optional for bootstrap-only usage)

### Setup Steps

**Step 1: Install probegen locally**
```bash
pip install probegen
```

**Step 2: Run interactive initialisation**
```bash
probegen init
```

`probegen init` does the following interactively:
1. Scans the repository for likely behavior-defining artifacts and proposes `behavior_artifacts.paths`
2. Scans for likely guardrail artifacts and proposes `guardrail_artifacts.paths`
3. Asks which eval platform(s) are in use and writes the `platforms` block
4. For each detected artifact, asks which dataset covers it and writes `mappings` if the user already has eval coverage
5. Asks whether to create a `context/` directory and generates stub files if yes
6. Writes `probegen.yaml` to the repository root
7. Copies the workflow file to `.github/workflows/probegen.yml`
8. Prints the list of GitHub secrets to add

**Step 3: Fill in context pack (strongly recommended)**

Fill in the generated stub files in `context/`. At minimum, complete `product.md` and `bad_examples.md`. These two files have the highest impact on probe quality. They are especially important in bootstrap mode, where Probegen has no existing eval corpus to compare against.

**Step 4: Add GitHub Secrets**

Navigate to: Repository → Settings → Secrets and variables → Actions

| Secret | Required | Source |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Required | console.anthropic.com → API Keys |
| `OPENAI_API_KEY` | ✅ Required for coverage-aware mode | platform.openai.com → API Keys |
| `LANGSMITH_API_KEY` | If using LangSmith | smith.langchain.com → Settings → API Keys |
| `BRAINTRUST_API_KEY` | If using Braintrust | braintrust.dev → Settings |
| `PHOENIX_API_KEY` | If using Arize Phoenix | app.phoenix.arize.com → Settings |

`GITHUB_TOKEN` is provided automatically by GitHub Actions. No action required.

**Step 4b: Create the approval label**

```bash
gh label create "probegen:approve" --color 0075ca --description "Approve Probegen probe writeback"
```

This label must exist before anyone can approve probes for writeback. Creating it is a one-time step per repository.

**Step 5: Verify setup**

```bash
probegen doctor
```

Checks API keys, hint pattern matches, and context file completeness. Fix any ✗ items before opening your first PR.

**Step 6: Open a PR**

Open any PR touching a behavior-defining or guardrail artifact — any file matched by your `probegen.yaml` `behavior_artifacts.paths` or `guardrail_artifacts.paths`. The `probegen-analyze` job runs automatically.

### `probegen.yaml` Full Reference

```yaml
version: 1

# ── Artifact Detection ───────────────────────────────────────────────────────
behavior_artifacts:
  paths:
    - "prompts/**"
    - "agents/**/*.md"
    - "config/agent_*.yaml"
  python_patterns:
    - "*_prompt"
    - "*_instruction"
    - "system_*"
    - "*_template"
  exclude:
    - "tests/**"
    - "*.test.yaml"
    - "docs/**"

guardrail_artifacts:
  paths:
    - "judges/**"
    - "validators/**"
    - "guardrails/**"
    - "classifiers/**"
  python_patterns:
    - "*_judge*"
    - "*_validator*"
    - "*_classifier*"
    - "*_filter*"
    - "*_rubric*"

# ── Context Pack ─────────────────────────────────────────────────────────────
context:
  product: "context/product.md"
  users: "context/users.md"
  interactions: "context/interactions.md"
  good_examples: "context/good_examples.md"
  bad_examples: "context/bad_examples.md"
  traces_dir: "context/traces/"
  trace_max_samples: 20

# ── Platform Configuration ───────────────────────────────────────────────────
platforms:
  langsmith:
    api_key_env: LANGSMITH_API_KEY       # env var name, not the value
  braintrust:
    api_key_env: BRAINTRUST_API_KEY
    org: "my-org"
  arize_phoenix:
    api_key_env: PHOENIX_API_KEY
    base_url: "https://app.phoenix.arize.com"
  promptfoo:
    config_path: "promptfooconfig.yaml"  # relative to repo root

# ── Dataset Mappings ─────────────────────────────────────────────────────────
mappings:
  - artifact: "prompts/citation_agent/system_prompt.md"
    platform: langsmith
    dataset: "citation-agent-evals"
  - artifact: "agents/planner/**"
    platform: braintrust
    project: "PlannerAgent"
    dataset: "planner-regression"
  - artifact: "judges/citation_quality_rubric.md"
    platform: langsmith
    dataset: "citation-judge-calibration"
    eval_type: judge_calibration          # signals different probe generation mode

# ── Embedding ────────────────────────────────────────────────────────────────
embedding:
  model: "text-embedding-3-small"         # or: cohere, bge-small-en-v1.5
  cache_path: ".probegen/embedding_cache.db"

# ── Similarity Thresholds ────────────────────────────────────────────────────
similarity:
  duplicate_threshold: 0.88
  boundary_threshold: 0.72

# ── Probe Generation ─────────────────────────────────────────────────────────
generation:
  max_probes_surfaced: 8
  max_probes_generated: 20
  diversity_limit_per_gap: 2

# ── Approval ─────────────────────────────────────────────────────────────────
approval:
  label: "probegen:approve"

# ── Auto-Run ─────────────────────────────────────────────────────────────────
auto_run:
  enabled: true
  fail_on: regression_guard
  notify: pr_comment
```

---

## 14. Data Models and Schemas

### `EvalCase` (unified model across all platforms)

```python
@dataclass
class EvalCase:
    id: str
    source_platform: str          # "langsmith" | "braintrust" | "phoenix" | "promptfoo"
    source_dataset_id: str
    source_dataset_name: str
    input_raw: Union[str, dict, list]
    input_normalized: str         # flattened for embedding
    is_conversational: bool       # True if input_raw is a conversation list
    expected_output: Optional[str]
    rubric: Optional[str]
    assertion_type: Optional[str]
    metadata: dict
    tags: list[str]
    embedding: Optional[list[float]]
    embedding_model: Optional[str]
```

### `CoverageSummary`

```python
@dataclass
class CoverageSummary:
    total_relevant_cases: int
    cases_covering_changed_behavior: int
    coverage_ratio: float
    platform: Optional[str]
    dataset: Optional[str]
    mode: Literal["coverage_aware", "bootstrap"]
    corpus_status: Literal["available", "empty", "unavailable"]
    bootstrap_reason: Optional[str]   # required when mode == "bootstrap"
```

### `ProbeCase`

```python
@dataclass
class ProbeCase:
    probe_id: str
    gap_id: str
    probe_type: Literal[
        "regression_guard", "expected_improvement", "boundary_probe",
        "edge_case", "overcorrection_probe", "tool_selection_probe",
        "ambiguity_probe", "judge_calibration_probe"
    ]
    is_conversational: bool
    input: Union[str, dict, list]   # str for single-turn, list for multi-turn
    input_format: Literal["string", "dict", "conversation"]
    expected_behavior: str
    expected_behavior_type: Literal[
        "exact_output", "contains", "not_contains", "llm_rubric", "format_check"
    ]
    rubric: Optional[str]
    probe_rationale: str
    related_risk_flag: str
    nearest_existing_case_id: Optional[str]
    nearest_existing_similarity: Optional[float]
    specificity_confidence: float
    testability_confidence: float
    realism_confidence: float
    approved: bool
```

### Promptfoo Export Format

```yaml
# Generated by probegen v{version} — commit {sha} — {timestamp}
# Artifact: {artifact_path}
# Review before committing to eval suite.

description: "Probegen probes for {artifact_path} (PR #{pr_number})"

tests:
  - description: "[boundary_probe] probe_001 — No citation in conversational query"
    # Gap: Citation rule not scoped to register
    # Rationale: Tests boundary at register threshold introduced by citation rule change
    # Nearest existing: case_a3f2 (similarity: 0.74)
    vars:
      query: "So what do you think about climate policy generally?"
    assert:
      - type: llm-rubric
        value: >
          The response does NOT include a citation, footnote, or 'according to [source]'
          phrasing. It responds naturally and conversationally.
```

---

## 15. Error Handling and Failure Modes

### Stage Failure Behaviour

| Failure | Behaviour |
|---|---|
| Stage 1 Agent SDK timeout | Post comment: "Probegen analysis timed out. No probes generated." Exit 0 (non-blocking). |
| Stage 1 produces no changes | Post a minimal no-changes comment ("This PR does not modify any behavior-defining artifacts"). Exit 0. |
| Stage 2 MCP connection failure | Continue with file-only fallback. Post warning in PR comment: "Could not connect to {platform}. Coverage analysis skipped; probes generated without coverage context." |
| Stage 2 no dataset mapping | Post comment with specific mapping instructions. Stage 3 still runs without coverage context. |
| Stage 2 mapped dataset exists but contains zero evals | Switch to starter mode. Post warning in PR comment: "No existing eval cases were found. Probes were generated as starter coverage from the diff and product context." |
| Stage 2 no eval corpus exists at all | Switch to starter mode. Post warning in PR comment: "Running in starter mode — probes are grounded in your diff and product context. Add eval dataset mappings to unlock coverage-aware analysis." |
| Stage 3 all probes filtered as duplicates | Post comment: "All generated probes were too similar to existing evals. No new coverage gaps identified." |
| Stage 3 produces < 3 probes after filtering | Post whatever was generated. No minimum enforcement. |
| Stage 4 write failure | Post comment on merged PR: "Probe write failed: {error}. Probes available at {artifact_path}." Exit non-zero to flag the failure. |
| Anthropic API rate limit | Retry with exponential backoff × 3, then fail gracefully as above. |

### Missing Dataset Mapping Warning

When Stage 2 finds no mapping for a changed artifact:

```markdown
⚠️ **No eval dataset mapped for `prompts/citation_agent/system_prompt.md`**

Coverage analysis was skipped for this artifact. Probes were generated without 
existing coverage context, which may reduce their relevance.

To add a mapping, add the following to `probegen.yaml`:

```yaml
mappings:
  - artifact: "prompts/citation_agent/system_prompt.md"
    platform: langsmith       # or: braintrust, arize_phoenix, promptfoo
    dataset: "your-dataset-name"
```
```

### Context Pack Missing Warning

When no `context/` directory exists or key files are absent:

```markdown
⚠️ **Context pack not configured**

Probegen is running without product context, user profiles, or interaction patterns.
Probe quality will be significantly reduced — probes may not reflect your product's 
actual users or vocabulary.

Run `probegen init --context-only` to create a context pack.
```

### Empty Eval Corpus Warning

When Stage 2 finds a mapped dataset but no eval cases inside it:

```markdown
> ⚠️ **Setup issue:** No eval dataset mapped for `citation-agent-evals` — coverage analysis skipped for this artifact.
```

When no corpus exists at all:

```markdown
> ⚠️ **Starter mode** — Running in starter mode — probes are grounded in your diff and product context. Add eval dataset mappings to unlock coverage-aware analysis.
```

The internal schema value remains `mode: "bootstrap"`. "Starter mode" is the user-facing label shown in PR comments.

---

## 16. Out of Scope (v1)

The following are explicitly deferred to future versions:

**DeepEval integration** — DeepEval test cases are defined in Python code, not a structured data file. No clean programmatic read path exists. v1 exports probegen-generated probes as DeepEval Python stubs (in `.probegen/runs/{sha}/probes_deepeval.py`) but does not read existing DeepEval cases for coverage analysis. Teams using DeepEval should add a `deepeval_cases.json` export step to CI and point probegen at that file.

**Prompts stored in databases** — No reliable way to detect or retrieve runtime-fetched prompts without application-level hooks. If a prompt is fetched from a database at runtime and not tracked as a file, probegen will not detect changes to it. Documented workaround: add a `probegen export` call to the application that dumps current prompt state to a tracked file.

**Cross-artifact interaction effect analysis** — Analysis of how a change to a system prompt interacts with a concurrently unchanged tool description or retrieval instruction. Stage 1 analyzes changed artifacts; it does not reason about interactions with unchanged artifacts. Compound changes (both changed in the same PR) are detected and flagged; cross-artifact interactions where only one changed are deferred.

**GitHub App / PR comment command approval** — The `/probegen approve 1,3,5` comment command pattern requires a webhook server or GitHub App infrastructure. Deferred to v2. v1 uses label-only approval.

**Humanloop** — Platform sunsetted September 8, 2025. Not supported.

**Cost tracking dashboard** — Per-PR and per-repo cost tracking for Anthropic API usage. Stage costs are logged to `metadata.json` per run. Aggregation and dashboard deferred.
