# Parity — Technical Specification

**Version:** 1.1
**Status:** Implemented
**Audience:** Engineers working on or integrating with Parity

---

## Table of Contents

1. [What Is Parity](#1-what-is-parity)
2. [Purpose and Problem Statement](#2-purpose-and-problem-statement)
3. [Why This Approach](#3-why-this-approach)
4. [System Architecture Overview](#4-system-architecture-overview)
5. [Context Pack](#5-context-pack)
6. [Pipeline: Stage 1 — Change Detection and Intent Analysis](#6-pipeline-stage-1)
7. [Pipeline: Stage 2 — Coverage Gap Analysis](#7-pipeline-stage-2)
8. [Pipeline: Stage 3 — Probe Generation and Proposal](#8-pipeline-stage-3)
9. [Pipeline: Stage 4 — Platform Write](#9-pipeline-stage-4)
10. [Approval Mechanism](#10-approval-mechanism)
11. [Eval Platform Integrations](#11-eval-platform-integrations)
12. [GitHub Action and CI Integration](#12-github-action-and-ci-integration)
13. [User Setup and Configuration](#13-user-setup-and-configuration)
14. [Data Models and Schemas](#14-data-models-and-schemas)
15. [Error Handling and Failure Modes](#15-error-handling-and-failure-modes)
16. [Out of Scope (v1)](#16-out-of-scope-v1)

---

## 1. What Is Parity

Parity is a CI-integrated developer tool for teams building LLM agents and prompt-driven systems. It detects behaviorally significant changes in a pull request — changes to prompts, agent instructions, guardrails, validators, and related artifacts — and automatically generates targeted evaluation probes designed to test the behavioral consequences of those specific changes.

Parity is not an eval runner. It does not execute evals. It generates eval *inputs* — targeted test cases — that developers review and add to their existing evaluation pipelines. Its output feeds into whatever eval infrastructure the team already uses: LangSmith, Braintrust, Arize Phoenix, Promptfoo, or plain files. If no eval corpus exists yet, Parity still operates in bootstrap mode and proposes plausible starter evals grounded in the diff and available product context.

Parity runs as a non-blocking parallel job in GitHub Actions. It does not gate or delay merges. It surfaces a PR comment containing a ranked, rationale-annotated set of proposed eval probes for developer review, and writes approved probes to the eval platform after an explicit human approval step.

Parity should be framed as working out of the box, while improving with more coverage and more context. Like many LLM applications, the more evals teams already have and the more detail they provide about product behavior, users, and failure modes, the better the generated recommendations become.

> **Terminology note:** Parity uses "bootstrap mode" in internal code and configuration (field name `coverage_summary.mode == "bootstrap"`), and "Starter mode" in user-facing PR comments. They refer to the same concept: when no existing eval corpus is available, Parity generates starter coverage grounded in the diff and product context. Throughout this spec, we use the user-facing term "Starter mode."

---

## 2. Purpose and Problem Statement

### The Core Problem

Teams building LLM agents iterate on prompts, instructions, guardrails, and tool configurations continuously. Every one of these changes modifies agent behavior — sometimes in the intended direction, sometimes not. The standard practice is to maintain an eval dataset and run it in CI. The problem is structural: **eval datasets are authored once, rarely expanded, and are almost never updated in proportion to the behavioral changes being made.**

When a developer changes a system prompt to fix a citation bug, the existing evals test what was important three months ago. The new edge cases introduced by the change — the register-scoping ambiguity, the overcorrection into non-factual contexts, the boundary between what triggers a citation and what doesn't — go untested. The regression ships.

This is not a discipline problem. It is a tooling problem. The cost of writing good eval cases is high, and the developer who changed the prompt — the person most qualified to know what it might break — is also the most likely to be blind to the risks.

### The Specific Gaps Parity Fills

**Gap 1: Change-coupled coverage.** No existing tool analyzes what a specific diff is likely to break and generates eval cases for that specific change. Tools like LangSmith, Braintrust, and Promptfoo manage and run evals. They do not generate coverage in response to changes.

**Gap 2: Guardrail change testing.** Changes to LLM judges, output classifiers, validators, and safety filters are at least as behaviorally significant as prompt changes, and are rarely tested with the same rigor. A loosened judge rubric means your evals look better while your agent gets worse.

**Gap 3: Contextually grounded probes.** Generic probe generation produces generic probes. Probes that reflect the actual product, the actual users, the actual vocabulary, and the actual failure history of the system are categorically more valuable.

**Gap 4: Real interaction grounding.** Production traces and real user interactions encode tone, vocabulary, and edge case distributions that no developer-authored eval dataset fully captures.

---

## 3. Why This Approach

### Why Claude Agent SDK as the Harness

Parity uses the Claude Agent SDK (Python) as its primary orchestration mechanism for the reasoning-intensive pipeline stages.

The Agent SDK gives Claude Code's full agent loop — codebase traversal, tool execution, MCP integration, hooks, session management — as a programmatically controllable Python library. This is the correct harness for parity because:

- **Codebase variance is the hardest problem.** Users store prompts in Python constants, YAML files, markdown, Jinja templates, and environment variables. A custom traversal layer would need to anticipate every pattern. The Agent SDK encounters the actual codebase and reasons about what it finds.
- **Structured message streaming.** `AssistantMessage`, `ResultMessage`, and `UserMessage` events allow parity to intercept outputs, validate intermediate results, and handle errors without stdout parsing.
- **Deterministic write path.** Stage 4 stays outside the Agent SDK entirely, so approved-platform writes remain fast, auditable, and isolated from agent tool access.
- **Spend control with stage-specific caps.** Parity derives stage caps from one total analysis spend cap, then passes the agent-stage caps into `max_budget_usd`.
- **MCP first-class.** Platform integrations via MCP are native to the SDK, not shell-invoked.

The Claude Agent SDK's CLI (`claude code --print ...`) is designed for one-shot scripting. Parity, however, uses the Agent SDK as a Python library to orchestrate multi-stage reasoning. This allows Parity to maintain state across stages, parse structured outputs, and implement approval gates — features that require programmatic control, not CLI automation.

### Why Phased Stages With JSON Handoffs

The pipeline runs as four discrete stages, each a separate Agent SDK invocation, with structured JSON artifacts as the handoff between them.

**Not session-continuous between stages.** Cross-stage session continuity would carry the codebase traversal context from Stage 1 into Stage 2's eval retrieval reasoning. That context is valuable for Stage 1's intent inference but pollutes Stage 2's coverage analysis. Each stage receives exactly the fields it needs from prior stages, extracted from the JSON artifact. Stages are independently retryable.

**Not a single long-running agent.** A single agent session spanning detection through generation accumulates context that degrades reasoning quality (context rot) and makes failures hard to isolate and retry.

**JSON handoffs are precise.** Prose report handoffs are verbose, ambiguous, and cause downstream stages to anchor on the report's framing. Structured JSON passes conclusions, not reasoning traces.

### Why Non-Blocking CI

Parity does not gate merges. It runs as a parallel job. The rationale:

- Eval generation is a review aid, not a correctness gate. Blocking CI on probe generation would be wrong in the same way that blocking CI on a linter's suggestions would be wrong.
- Probe generation has variable latency. Agent SDK sessions involving codebase traversal and external MCP calls are not fast enough to be on the critical path.
- Trust requires low friction. If parity ever slows a merge, it will be disabled.

### Why a Hard Approval Gate

Probes are not written to eval platforms until a developer explicitly approves them. The hard gate (not soft) is correct because:

- Soft gates (write but mark pending) pollute eval datasets and create false coverage signals. Teams look at dataset size and feel covered.
- Unapproved probes in a platform accumulate into a backlog nobody processes.
- Eval suite quality depends on intentional authorship. Parity generates candidates; developers own what goes in.

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
│              NO → post "no changes" comment, stop   │
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
        │  Developer applies label: parity:approve
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  Stage 4: Platform Write                            │
│  Triggered: PR merge + parity:approve label       │
│  [write_probes.py — direct platform SDK calls]      │
│  [post write-outcome comment to merged PR]          │
└─────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| Claude Agent SDK (Stages 1–3) | Reasoning, codebase traversal, in-process tool orchestration, probe generation |
| `get_behavior_diff` tool | Orchestrator-invoked (pre-Stage 1): git diff + PR metadata → structured `RawChangeData` JSON. Injected into Stage 1 prompt; not agent-called. |
| `embed_batch` tool | Deterministic: batch embed eval inputs, cache results |
| `find_similar` tool | Deterministic: cosine similarity, classify as duplicate/boundary/related/novel |
| Stage 2 SDK MCP toolbox | Host-owned read/compare tools: `search_eval_targets`, `fetch_eval_cases`, `embed_batch`, `find_similar`, `find_similar_batch` |
| `write_probes.py` (Stage 4) | Deterministic: approved probes → platform SDK write calls. No agent involved. |
| Context Pack | Static files: product context, user profiles, interaction patterns, good/bad examples, traces |

**Hint patterns are optimization hints, not gates.** The `behavior_artifacts.paths` and `guardrail_artifacts.paths` patterns in `parity.yaml` are used to pre-load files with content for Stage 1's efficiency, not to filter what the agent analyzes. Stage 1 receives all changed files in the PR and decides what matters. Files matching patterns are just fetched faster.

### RawChangeData Contract: Orchestrator-to-Stage-1 Handoff

The `get_behavior_diff` CLI tool is invoked by the orchestrator before Stage 1 begins. It wraps three data sources (git diff, GitHub webhook payload, pre-loaded hint match content) and returns a single structured JSON object — the `RawChangeData` — which is injected into the Stage 1 prompt.

#### Invocation

```bash
parity get-behavior-diff \
  --base-branch main \
  --pr-number 142 \
  --config parity.yaml
```

#### Input Sources

**Source 1 — Git diff (unfiltered)**
`git diff origin/{base_branch}...HEAD` fetches all changed files in the PR with no pathspec filtering. Config patterns are used only to decide which files to pre-load with full content (see Source 3).

**Source 2 — GitHub event payload**
Read from `$GITHUB_EVENT_PATH` (webhook payload schema post-October 2025). Provides:
- `pull_request.number`, `.title`, `.body`, `.base.ref`, `.head.sha`
- `pull_request.labels[]`, `.user.login`
- `repository.full_name`

**Source 3 — Pre-loaded hint match content**
For files matching `behavior_artifacts.paths` or `guardrail_artifacts.paths` patterns: full before/after content and a unified diff with 5 lines of context. Non-matching files are listed in `all_changed_files` but not pre-loaded — Stage 1 fetches them via tools if relevant.

#### Output: RawChangeData Schema

```json
{
  "schema_version": "1.0",
  "pr_number": 142,
  "pr_title": "Add citation requirement to CitationAgent",
  "pr_body": "This change adds a rule requiring the agent to...",
  "pr_labels": ["enhancement", "prompts"],
  "base_branch": "main",
  "head_sha": "abc123def456",
  "repo_full_name": "org/repo",
  "all_changed_files": [
    { "path": "prompts/citation_agent/system_prompt.md", "change_kind": "modification", "renamed_from": null },
    { "path": "src/config.py", "change_kind": "modification", "renamed_from": null }
  ],
  "hint_matched_artifacts": [
    {
      "path": "prompts/citation_agent/system_prompt.md",
      "artifact_class": "behavior_defining",
      "artifact_type": "system_prompt",
      "change_kind": "modification",
      "before_content": "You are a helpful assistant...",
      "after_content": "You are a helpful assistant. Always cite sources...",
      "raw_diff": "@@ -3,4 +3,5 @@\n You are a helpful assistant.\n+Always cite sources when answering factual questions.\n",
      "before_sha": "sha256:aabbcc...",
      "after_sha": "sha256:ddeeff..."
    }
  ],
  "hint_patterns": {
    "behavior_paths": ["prompts/**", "agents/**/*.md"],
    "guardrail_paths": ["judges/**", "validators/**"],
    "behavior_python_patterns": ["*_prompt", "*_instruction", "system_*"],
    "guardrail_python_patterns": ["*_judge*", "*_validator*", "*_classifier*"]
  },
  "unchanged_hint_matches": [
    "prompts/planner/planner_prompt.md"
  ],
  "has_changes": true,
  "artifact_count": 2
}
```

**Field notes:**
- `all_changed_files` — every file modified, added, deleted, or renamed in the PR. No filtering. `artifact_count` equals `len(all_changed_files)`.
- `hint_matched_artifacts` — files matching configured hint patterns, pre-loaded with before/after content for efficiency. A subset of `all_changed_files`.
- `hint_patterns` — the patterns from `parity.yaml`, passed as agent guidance (not gates).
- `unchanged_hint_matches` — tracked files matching `behavior_artifacts.paths` that are NOT in this PR. Gives the agent context about stability.
- `has_changes` — true if `all_changed_files` is non-empty.

#### Return Codes

| Code | Meaning |
|---|---|
| 0 | Success, JSON written to stdout |
| 1 | Git error (no history, bad base branch) |
| 2 | `GITHUB_EVENT_PATH` not set or malformed |
| 3 | `parity.yaml` not found or invalid |

When `has_changes` is false, the JSON is still valid and complete — `all_changed_files` is empty. Stage 1 produces a `BehaviorChangeManifest` with `has_changes: false`, which gates out Stages 2–3. The workflow posts a "no behavioral changes detected" comment.

---

## 5. Context Pack

The Context Pack is the primary driver of probe specificity and quality. It is the set of contextual documents parity injects into Stage 1 and Stage 3 to ground the agent's understanding of the product, its users, and what good and bad behavior looks like.

Without a Context Pack, parity generates probes that are technically correct but generically framed. With a Context Pack, probes reflect the actual product, the actual users, the actual vocabulary, and the actual failure history.

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

> **Important — PII and sanitisation responsibility:** Production traces may contain personally identifiable information or sensitive user data. Parity does not sanitise traces. The user is solely responsible for ensuring that any trace data provided in the Context Pack has been appropriately anonymised before being committed to the repository or supplied to parity. This requirement must be documented prominently in onboarding.

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
# parity.yaml
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

### `parity init` Context Pack Prompting

During `parity init`, you're asked whether to create stub files for the context pack:

```
5. Create a context/ directory with stub files? [Y/n]:
```

If you choose yes, Parity creates six files with section headers and prompts:
- `context/product.md` — What your product does and who uses it
- `context/users.md` — User profiles and personas
- `context/interactions.md` — Common flows and interaction patterns
- `context/good_examples.md` — Examples of correct agent behavior
- `context/bad_examples.md` — Known failure modes and edge cases
- `context/traces/README.md` — (optional) Information about adding production trace samples

Open these files in your editor and fill in each section. The section headers explain what to write.

**Context pack impact:** Parity works significantly better with a filled-in context pack. Without it, probes are generic ("Does the agent respond helpfully?"). With it, probes are specific to your product ("Does the citation rule still apply to customer service queries?"). Parity functions without context but emits a warning indicating probe quality will be significantly reduced.

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
    prompt=render_stage1_prompt(raw_change_data, context),
    options=ClaudeAgentOptions(
        tools=["Bash", "Read", "Glob"],
        can_use_tool=read_only_stage1_policy(repo_root),
        mcp_servers=[],          # no MCP in Stage 1
        max_turns=40,
        max_budget_usd=resolved_spend.stage1_agent_cap_usd,
        cwd=repo_root,
    )
):
    handle_message(message)
```

### Tools Available

**`Bash`, `Read`, `Glob`** — the agent's primary discovery tools. Stage 1 uses these to:
- Fetch file content for changed files not pre-loaded: `git show origin/{base}:{path}`, `git diff ... -- {path}`
- Inspect unchanged supporting files across the repo when they are needed to understand a changed artifact
- Inspect Python files for module-level string constants that act as prompts

`Bash` is restricted to read-only git inspection commands, `Read` and `Glob` are confined to the repository root, and secret-bearing or generated paths are denied.

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
3. Inspect unchanged supporting files when needed to understand the behavioral effect
   of a change. Use Read and Glob to follow imports, referenced templates,
   validators, schemas, and helper modules across the repo.
4. Behavioral artifacts include (not limited to): system prompts, tool descriptions,
   LLM judges, output validators, guardrails, retrieval instructions, retry policies,
   fallback prompts — any file whose change alters what the LLM agent does or decides.
5. For Python files: look for module-level string constants that contain prompt-like content.
6. Classify each behavioral artifact you discover. For each:
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
manifest = load_json(".parity/stage1.json")
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
    prompt=render_stage2_prompt(
        stage1_manifest,
        mapping_resolutions=resolved_mappings,
        bootstrap_brief=bootstrap_brief,
    ),
    options=ClaudeAgentOptions(
        tools=[],  # no built-in Bash or write tools
        mcp_servers={"parity_stage2": in_process_sdk_mcp_server()},
        max_turns=40,
        max_budget_usd=resolved_spend.stage2_agent_cap_usd,
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
- resolved mapping metadata derived from `parity.yaml`
- a deterministic bootstrap brief derived from Stage 1 risk signals

Raw diffs are not passed to Stage 2. They were needed for Stage 1's reasoning; they are noise for Stage 2.

### Tools Available

Stage 2 exposes a host-owned in-process MCP toolbox rather than generic Bash:

| Tool | Responsibility |
|---|---|
| `fetch_eval_cases` | Load eval cases from LangSmith, Braintrust, Arize Phoenix, or repo-local Promptfoo configs |
| `search_eval_targets` | Limited platform-side discovery when mappings are unresolved or stale |
| `embed_batch` | Embed eval inputs with host-owned OpenAI credentials and cache control |
| `find_similar` | Single-candidate similarity classification |
| `find_similar_batch` | Scoped multi-candidate comparison against one resolved corpus |

The agent uses these tools to retrieve eval cases for the datasets mapped to the changed artifacts in `parity.yaml`. If a mapping is unresolved, missing, stale, or points to an empty dataset, Stage 2 can recover with limited same-platform discovery or fall back to bootstrap coverage instead of failing. Credentials stay in the host process; the agent does not receive raw API keys or a secret-bearing `.claude/mcp_servers.json`.

Similarity thresholds (configurable in `parity.yaml`):

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

1. **Explicit mapping** in `parity.yaml` — preferred starting point
2. **Limited same-platform recovery** — if the explicit target is missing, inaccessible, empty, or materially unrelated, use `search_eval_targets` on that same platform
3. **Same-platform discovery for unresolved artifacts** — if no mapping resolves, use `search_eval_targets` conservatively and record the retrieval path in `coverage_summary.retrieval_notes`

### Stage 2 Prompt Design

```
SYSTEM:
You are a coverage gap analyst for LLM-based agent evaluation suites. Your task is to 
retrieve existing eval coverage for a set of changed artifacts and identify where coverage 
is missing relative to the predicted behavioral impacts.

PROCESS:
1. For each explicit mapping, retrieve eval cases with fetch_eval_cases.
2. If an explicit target is missing, inaccessible, empty, or materially unrelated,
   call search_eval_targets on that same platform and retry fetch_eval_cases.
3. For unresolved artifacts, use limited same-platform discovery via search_eval_targets,
   then fetch_eval_cases for the chosen corpus.
4. If relevant eval cases are found, call embed_batch and then prefer find_similar_batch
   for semantically coherent slices that share one artifact context and one resolved corpus.
   - `embed_batch` may return `budget_exceeded: true`
   - when that happens, stop requesting more embeddings
   - you may still use any returned cached embeddings, but treat `missing_ids` as unembedded
   - continue in degraded partial/bootstrap mode rather than failing the stage
5. If relevant eval cases are found by any retrieval path, remain in coverage-aware mode:
   - set `coverage_summary.mode` to `coverage_aware`
   - set `coverage_summary.corpus_status` to `available`
   - if needed, explain non-standard retrieval details in `coverage_summary.retrieval_notes`
   - leave `coverage_summary.bootstrap_reason` empty
6. Classify each risk flag as: covered | boundary_shift | uncovered
7. For guardrail artifacts, analyze coverage in both directions:
   - Are there existing cases testing things the guardrail should catch?
   - Are there existing cases testing things the guardrail should allow through?
8. If no relevant eval cases exist, or the stage degrades before full comparison completes, switch to bootstrap mode when necessary:
   - mark `coverage_summary.mode` as `bootstrap`
   - mark `coverage_summary.corpus_status` as `empty` or `unavailable`
   - explain the reason in `coverage_summary.bootstrap_reason`
   - use `coverage_summary.retrieval_notes` only for extra context, not as a substitute for `bootstrap_reason`
   - emit baseline gaps with empty `nearest_existing_cases`
9. Identify the top-priority gaps: uncovered risk flags ranked by severity.
10. Output the CoverageGapManifest JSON.

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
    "retrieval_notes": null,
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
        tools=[],                 # Stage 3 is pure generation from prompt context
        mcp_servers=[],           # no MCP in Stage 3
        max_turns=25,
        max_budget_usd=resolved_spend.stage3_agent_cap_usd,
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

Generate up to `candidate_probe_pool_limit` candidate probes and return the full candidate
pool in `ProbeProposal.probes`. The host orchestrator reranks that pool, applies the
diversity filter, and keeps at most `proposal_probe_limit` final proposal probes for review.

Output ProbeProposal JSON only. No prose.
```

**Post-Generation Processing (Orchestrator)**

After the agent completes, the orchestrator (`stage3.py`) applies ranking and diversity filtering to the raw probes:
- `rank_probes()` — Sort candidates by quality and relevance
- `apply_diversity_limit()` — Filter to max 2 probes per gap (configurable via `generation.diversity_limit_per_gap`)

These steps happen in Python, not via agent tool calls. This separation keeps Stage 3's reasoning focused on generation quality, with filtering logic isolated in the orchestration layer.

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

### Proposal Count Controls

- `proposal_probe_limit` controls the final shortlist shown to the reviewer and written back on approval.
- `candidate_probe_pool_limit` controls how many raw candidates Stage 3 may generate before host-side reranking.
- Defaults: `proposal_probe_limit = 8`, `candidate_probe_pool_limit = 20`
- If `candidate_probe_pool_limit` is omitted, Parity derives it automatically from `proposal_probe_limit`.

### Diversity Filter

After generation, before surfacing:
1. Run `find_similar` on all candidate probes against the existing eval corpus — discard any scoring ≥ 0.88 (duplicate)
2. Re-classify 0.72–0.87 scorers as `boundary_probe`
3. Apply diversity cap: retain maximum 2 probes per `gap_id`
4. Rank remaining by composite score (see Ranking below)
5. Take top `proposal_probe_limit`

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
    "promptfoo": ".parity/runs/abc123/probes.yaml",
    "deepeval": null,
    "raw_json": ".parity/runs/abc123/probes.json"
  }
}
```

### PR Comment Format

```markdown
## 🔍 Parity: Behavioral Impact Detected

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

**To approve all probes:** Add label `parity:approve` to this PR.  
**Full proposal + rationale:** `.parity/runs/abc123/ProbeProposal.json`  
**Promptfoo export:** `.parity/runs/abc123/probes.yaml`
```

---

## 9. Pipeline: Stage 4 — Platform Write

### Purpose

After developer approval, write approved probes to the eval platform and trigger a scoped eval run to validate whether the predicted behavioral impacts occurred.

### Trigger

Two conditions must both be true:
1. PR is merged to the base branch
2. Label `parity:approve` was applied to the PR at any point before merge

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
            "generated_by": "parity",
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
            "generated_by": "parity",
            "probe_id": p.probe_id,
        },
        "tags": [p.probe_type, "parity"],
    } for p in probes])
```

**Arize Phoenix** (MCP write confirmed, but direct SDK used for reliability in CI):
```python
from phoenix.client import Client

def write_to_phoenix(probes, dataset_name, base_url: str, api_key: str):
    client = Client(base_url=base_url, api_key=api_key)
    # Using arize-phoenix-client==2.0.0 API (see DECISIONS.md)
    dataset = client.datasets.create(
        name=dataset_name,
        description=f"Parity auto-generated probes",
    )
    for probe in probes:
        dataset.append({
            "input": probe.input,
            "expected_behavior": probe.expected_behavior,
            "probe_type": probe.probe_type,
            "rationale": probe.rationale,
        })
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

### Write-Outcome Comment

After write completes, a follow-up deterministic step posts a comment on the merged PR reporting the outcome:

```markdown
## ✅ Parity: Probes Written

**7 probes written to:** `citation-agent-evals` (LangSmith)
```

If write fails partially or entirely, the comment describes which targets failed and where the probe artifacts can be retrieved from GitHub Actions.

### Auto-Run (Planned — v2)

> **Not yet implemented.** The `auto_run` configuration block is parsed and stored but not executed in v1.

After write completes, a future v2 release will trigger an eval run scoped to the newly added probes and post results back to the merged PR. Platform-specific trigger mechanisms:

- **LangSmith:** REST API call filtered by `generated_by: parity` and `source_commit: {sha}`
- **Braintrust:** `braintrust eval` CLI with tag filter `parity`
- **Arize Phoenix:** `phoenix.client.Client().run_experiment()` scoped to the new dataset entries
- **Promptfoo:** `promptfoo eval --filter-description "parity"`

The `auto_run` config block is present in the schema for forward compatibility but has no effect in v1:

```yaml
# auto_run:
#   enabled: true
#   fail_on: regression_guard
#   notify: pr_comment
```

---

## 10. Approval Mechanism

### v1 Mechanism: GitHub Label

The developer applies the label `parity:approve` to the PR. This is the sole v1 approval mechanism.

**Why label over other options:**
- Requires no additional infrastructure beyond a second workflow file
- Native to GitHub PR review flow
- Asynchronous — developer approves when ready, not when the pipeline completes
- Does not require a GitHub App or webhook server
- Works naturally with PR review processes (team lead reviews probes, applies label)

**Partial approval:** Not supported in v1. The label is binary. The developer reviews all proposed probes in the PR comment, mentally dismisses any they disagree with, and applies the label when satisfied with the remaining set. Unapproved probes (those the developer chose not to include) are discarded — the `ProbeProposal.json` artifact is retained for reference but no further action is taken on unapproved probes.

**Label configuration:**
```yaml
# parity.yaml
approval:
  label: "parity:approve"    # customisable
```

### Workflow Trigger for Stage 4

The Stage 4 workflow is triggered when:
1. A PR is merged
2. The PR has the `parity:approve` label
3. The trigger is `pull_request_target` (not `pull_request`) for secure access to secrets

```yaml
on:
  pull_request_target:
    types: [closed]

permissions:
  actions: read          # Required: resolve-run-id queries GitHub Actions API
  contents: read
  pull-requests: write

jobs:
  parity-write:
    if: |
      github.event.pull_request.merged == true &&
      contains(github.event.pull_request.labels.*.name, 'parity:approve')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}
          persist-credentials: false

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install parity-ai

      - name: Resolve analysis run
        id: resolve
        run: |
          run_id=$(parity resolve-run-id \
            --repo ${{ github.repository }} \
            --workflow-id parity.yml \
            --head-sha ${{ github.event.pull_request.head.sha }})
          echo "run_id=$run_id" >> $GITHUB_OUTPUT
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Download probe proposal
        uses: actions/download-artifact@v4
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ steps.resolve.outputs.run_id }}
          name: parity-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .parity/

      - name: Write probes to platform
        id: write
        continue-on-error: true
        run: |
          parity write-probes \
            --proposal .parity/stage3.json \
            --config parity.yaml \
            --outcome-output .parity/write-outcome.json \
            --skip-comment
        env:
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}

      - name: Post writeback result comment
        if: always() && steps.write.outcome != 'skipped'
        run: |
          parity post-write-comment \
            --outcome .parity/write-outcome.json \
            --pr-number ${{ github.event.pull_request.number }}
        env:
          PR_NUMBER: ${{ github.event.pull_request.number }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_RUN_ID: ${{ github.run_id }}
```

---

## 11. Eval Platform Integrations

### Integration Matrix

| Platform | Stage 2 Read Path | Write Method | Auto-Run | Notes |
|---|---|---|---|---|
| LangSmith | Host-owned SDK reader | Python SDK direct | v2 (planned) | Stage 2 wraps LangSmith access behind in-process tools |
| Braintrust | Host-owned direct reader | Python SDK direct | v2 (planned) | Stage 2 wraps Braintrust access behind in-process tools |
| Arize Phoenix | Host-owned SDK reader | Python SDK direct | v2 (planned) | Stage 2 wraps Phoenix access behind in-process tools |
| Promptfoo | Repo-local file reader | File append | v2 (planned) | Read and write stay confined to local YAML files |
| Humanloop | ❌ Sunsetted Sept 2025 | N/A | N/A | Removed from scope entirely |

### MCP Configuration Generation

`parity setup-mcp` generates `.claude/mcp_servers.json` from `parity.yaml` + available env vars for local debugging only. The CI analyzer path does not write this file; Stage 2 uses an in-process SDK MCP toolbox instead.

```python
def generate_mcp_config(config: ParityConfig, env: dict) -> dict:
    servers = {}
    
    if env.get("LANGSMITH_API_KEY") and config.platforms.langsmith:
        servers["langsmith"] = {
            "command": "uvx",
            "args": ["langsmith-mcp-server"],
            "env": {"LANGSMITH_API_KEY": env["LANGSMITH_API_KEY"]}
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

Regardless of platform integration, every Stage 3 run writes to `.parity/runs/{commit_sha}/`:

```
.parity/runs/{commit_sha}/
  ├── BehaviorChangeManifest.json     # Stage 1 output
  ├── CoverageGapManifest.json        # Stage 2 output
  ├── ProbeProposal.json              # Stage 3 output (all probes, full detail)
  ├── probes.yaml                     # Promptfoo-compatible, ready to use
  ├── summary.md                      # Human-readable full probe list with rationale
  └── metadata.json                   # Run metadata: model, cost, duration, versions
```

These artifacts are uploaded as GitHub Actions artifacts and retained for 90 days (configurable).

### Prompt Rendering Contract

Each stage prompt is a Python function in `parity/prompts/` that assembles a structured input (RawChangeData, manifests, context pack) into a plain f-string template. No Jinja or external templating.

#### Token Budgets and Truncation Strategy

Stage 3 uses an internal input-context packing limit rather than a user-facing token knob. The limit is derived from the model context window minus reserved response headroom, and the reserved response headroom grows with `candidate_probe_pool_limit`. At the default pool size of 20 candidates, the current input packing target is 80,000 tokens.

Token allocation by section:

| Section | Token Budget | Truncation Strategy |
|---|---|---|
| System prompt template | ~3,000 | Fixed |
| Stage 1 stripped manifest | ~2,000 | Fixed (structured JSON, already small) |
| Stage 2 gap manifest | ~3,000 | Fixed (structured JSON) |
| `product.md` | 4,000 | Hard truncate, append `[truncated]` |
| `users.md` | 2,000 | Hard truncate |
| `interactions.md` | 3,000 | Hard truncate |
| `good_examples.md` | 3,000 | Hard truncate |
| `bad_examples.md` | 4,000 | Hard truncate — highest-value section |
| Trace samples | 6,000 | Random sample up to `trace_max_samples`, then truncate each to 300 tokens |
| Nearest existing cases | 4,000 | Top 5 per gap, each truncated to 200 tokens |

Token counting uses `tiktoken` with `cl100k_base` encoding. If the rendered prompt exceeds the derived Stage 3 input packing limit, the fallback pass applies: reduce `good_examples` (3,000 → 1,500), reduce `bad_examples` (4,000 → 2,000), and drop trace samples entirely. Fixed-budget sections are not reduced. No further retry.

#### Stage 1 Rendering

```python
def render_stage1_prompt(raw_change_data: dict, context: ContextPack) -> str:
    # ... extracts PR metadata, hint patterns from raw_change_data
    return STAGE1_SYSTEM_TEMPLATE.format(
        product_context=truncate(context.product, 4000),
        bad_examples=truncate(context.bad_examples, 4000),
        pr_metadata_json=json.dumps(pr_metadata, indent=2),
        all_changed_files_json=json.dumps(raw_change_data.get("all_changed_files", []), indent=2),
        hint_matched_artifacts_json=json.dumps(raw_change_data.get("hint_matched_artifacts", []), indent=2),
        hint_patterns_json=json.dumps(hint_patterns, indent=2),
        base_branch=raw_change_data.get("base_branch", "main"),
        python_patterns_hint="...",
    )
```

**Stage 1 receives:** product.md, bad_examples.md, and RawChangeData (PR metadata, all_changed_files, hint_matched_artifacts, hint_patterns). **Does NOT receive:** users.md, interactions.md, good_examples.md, traces. Agent fetches additional files via Read/Bash/Glob tools as needed.

#### Stage 2 Rendering

```python
def render_stage2_prompt(stage1_manifest: dict) -> str:
    stripped = strip_raw_diffs(stage1_manifest)  # remove before/after content, raw_diff
    return STAGE2_SYSTEM_TEMPLATE.format(
        manifest_json=json.dumps(stripped, indent=2),
    )
```

**Stage 2 receives:** The stripped Stage 1 manifest, resolved mapping metadata, and a deterministic bootstrap brief. No context pack. Host-owned Stage 2 tools provide eval corpus data dynamically. If zero relevant eval cases are found, Stage 2 produces `CoverageGapManifest` in bootstrap mode with `coverage_summary.mode = "bootstrap"` and empty `nearest_existing_cases` arrays.

#### Stage 3 Rendering

```python
def render_stage3_prompt(stage1_manifest: dict, stage2_manifest: dict, context: ContextPack) -> str:
    stage1_brief = extract_stage1_brief(stage1_manifest)  # intent + risk flags only
    traces = sample_traces(context.traces_dir, max_samples=context.trace_max_samples)
    return STAGE3_SYSTEM_TEMPLATE.format(
        product_context=truncate(context.product, 4000),
        users_context=truncate(context.users, 2000),
        interactions_context=truncate(context.interactions, 3000),
        good_examples=truncate(context.good_examples, 3000),
        bad_examples=truncate(context.bad_examples, 4000),
        trace_samples=format_traces(traces, max_tokens_each=300, total_budget=6000),
        stage1_brief_json=json.dumps(stage1_brief, indent=2),
        coverage_summary_json=json.dumps(stage2_manifest["coverage_summary"], indent=2),
        gaps_json=json.dumps(stage2_manifest["gaps"], indent=2),
        nearest_cases_json=format_nearest_cases(stage2_manifest["gaps"], max_per_gap=5),
    )
```

**Stage 3 receives:** Full context pack, Stage 1 brief (intent + risk flags only, raw diffs/content stripped), Stage 2 coverage summary and gaps. This is the primary quality driver where the generator sees product context, users, patterns, examples, and real traces.

### Embedding and Similarity Tools: `embed_batch`, `find_similar`, and `find_similar_batch`

These utilities remain available as Python CLI entry points for debugging and local workflows. In the analyzer pipeline, Stage 2 now calls equivalent host-owned SDK MCP tools rather than shelling out through Bash.

#### `embed_batch` Tool

**Invocation:**
```bash
parity embed-batch \
  --inputs /tmp/inputs.json \
  --output /tmp/embeddings.json \
  --model text-embedding-3-small \
  --cache .parity/embedding_cache.db
```

**Input schema (`inputs.json`):**
```json
[
  { "id": "case_a3f2", "text": "What year was the Paris Agreement signed?" },
  { "id": "case_b17d", "text": "SYSTEM: ...\nUSER: Tell me about climate policy" }
]
```

**Output schema (`embeddings.json`):**
```json
[
  {
    "id": "case_a3f2",
    "text_hash": "sha256:aabb...",
    "embedding": [0.0023, -0.0093, ...],
    "model": "text-embedding-3-small",
    "dimensions": 1536,
    "cached": false
  }
]
```

**Cache behavior:** Compute `sha256(id + text)`. Check cache by `(id, text_hash, model)`. On hit, set `cached: true`, skip API call. Batch non-cached inputs into a single OpenAI API call. Return codes: 0 success (including cache degradation — embeddings still written, warning emitted to stderr), 1 API error.

#### `find_similar` Tool

**Invocation:**
```bash
parity find-similar \
  --candidate /tmp/candidate.json \
  --corpus /tmp/embeddings.json \
  --output /tmp/similarity.json \
  --duplicate-threshold 0.88 \
  --boundary-threshold 0.72
```

**Candidate schema (`candidate.json`):**
```json
{ "id": "probe_001_candidate", "text": "So what do you think about climate policy generally?" }
```

**Output schema (`similarity.json`):**
```json
{
  "candidate_id": "probe_001_candidate",
  "results": [
    { "corpus_id": "case_a3f2", "similarity": 0.74, "classification": "boundary" },
    { "corpus_id": "case_b17d", "similarity": 0.43, "classification": "novel" }
  ],
  "top_match": { "corpus_id": "case_a3f2", "similarity": 0.74, "classification": "boundary" },
  "max_similarity": 0.74,
  "overall_classification": "boundary"
}
```

**Classification ranges:** `duplicate` (≥0.88), `boundary` (0.72–0.87), `related` (0.50–0.71), `novel` (<0.50). Thresholds configurable via CLI and `parity.yaml`.

---

## 12. GitHub Action and CI Integration

### Full Workflow File

```yaml
# .github/workflows/parity.yml
name: Parity

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
  parity-analyze:
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
          pip install parity-ai
          npm install -g @anthropic-ai/claude-code

      - name: Stage 1 — Change Detection
        run: |
          parity run-stage 1 \
            --pr-number ${{ github.event.pull_request.number }} \
            --base-branch ${{ github.event.pull_request.base.ref }} \
            --output .parity/stage1.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_EVENT_PATH: ${{ github.event_path }}

      - name: Check gate
        id: gate
        run: |
          has_changes=$(python -c "
          import json
          m = json.load(open('.parity/stage1.json'))
          print('true' if m.get('has_changes') else 'false')
          ")
          echo "has_changes=$has_changes" >> $GITHUB_OUTPUT

      - name: Stage 2 — Coverage Analysis
        if: steps.gate.outputs.has_changes == 'true'
        run: |
          parity run-stage 2 \
            --manifest .parity/stage1.json \
            --output .parity/stage2.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}

      - name: Stage 3 — Probe Generation
        if: steps.gate.outputs.has_changes == 'true'
        run: |
          parity run-stage 3 \
            --manifest .parity/stage1.json \
            --gaps .parity/stage2.json \
            --output .parity/stage3.json
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: parity-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .parity/
          retention-days: 90

  parity-comment:
    if: github.event_name == 'pull_request'
    needs: parity-analyze
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      pull-requests: write

    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install parity-ai

      - name: Download analysis artifact
        uses: actions/download-artifact@v4
        with:
          name: parity-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .parity/

      - name: Post PR comment (no changes)
        if: needs.parity-analyze.outputs.has_changes == 'false'
        run: parity post-comment --no-changes --pr-number ${{ github.event.pull_request.number }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Post PR comment (probes)
        if: needs.parity-analyze.outputs.has_changes == 'true'
        run: |
          parity post-comment \
            --proposal .parity/stage3.json \
            --pr-number ${{ github.event.pull_request.number }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  # ─── STAGE 4: Write + Auto-Run (post-merge, post-approval) ──────────────────
  parity-write:
    if: |
      github.event_name == 'pull_request_target' &&
      github.event.pull_request.merged == true &&
      contains(github.event.pull_request.labels.*.name, 'parity:approve')
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.merge_commit_sha }}
          persist-credentials: false

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install parity-ai

      - name: Resolve analysis run
        id: resolve
        run: |
          run_id=$(parity resolve-run-id \
            --repo ${{ github.repository }} \
            --workflow-id parity.yml \
            --head-sha ${{ github.event.pull_request.head.sha }})
          echo "run_id=$run_id" >> $GITHUB_OUTPUT
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Download probe proposal
        uses: actions/download-artifact@v4
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ steps.resolve.outputs.run_id }}
          name: parity-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
          path: .parity/

      - name: Write probes to platform
        id: write
        continue-on-error: true
        run: |
          parity write-probes \
            --proposal .parity/stage3.json \
            --config parity.yaml \
            --outcome-output .parity/write-outcome.json \
            --skip-comment
        env:
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}

      - name: Post writeback result comment
        if: always() && steps.write.outcome != 'skipped'
        run: |
          parity post-write-comment \
            --outcome .parity/write-outcome.json \
            --pr-number ${{ github.event.pull_request.number }}
        env:
          PR_NUMBER: ${{ github.event.pull_request.number }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_RUN_ID: ${{ github.run_id }}
```

### Non-Blocking Guarantee

The `parity-analyze` job runs in parallel with all other CI jobs and is never referenced in branch protection rules. It cannot block a merge. It is a review aid.

---

## 13. User Setup and Configuration

### Prerequisites

- Node.js 22+ (for Claude Code CLI, required by Agent SDK)
- Python 3.11+
- GitHub Actions enabled on the repository
- Anthropic API key (required)
- At least one eval platform API key if you want direct platform integration or automatic writeback (optional for bootstrap-only usage)

### Setup Steps

**Step 1: Install parity locally**
```bash
pip install parity-ai
```

**Step 2: Run interactive initialisation**
```bash
parity init
```

`parity init` does the following interactively:
1. Scans the repository for likely behavior-defining artifacts and proposes `behavior_artifacts.paths`
2. Scans for likely guardrail artifacts and proposes `guardrail_artifacts.paths`
3. Asks which eval platform(s) are in use and writes the `platforms` block
4. For each detected artifact, asks which dataset covers it and writes `mappings` if the user already has eval coverage
5. Asks whether to create a `context/` directory and generates stub files if yes
6. Writes `parity.yaml` to the repository root
7. Copies the workflow file to `.github/workflows/parity.yml`
8. Prints the list of GitHub secrets to add

**Step 3: Fill in context pack (strongly recommended)**

Fill in the generated stub files in `context/`. At minimum, complete `product.md` and `bad_examples.md`. These two files have the highest impact on probe quality. They are especially important in bootstrap mode, where Parity has no existing eval corpus to compare against.

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
gh label create "parity:approve" --color 0075ca --description "Approve Parity probe writeback"
```

This label must exist before anyone can approve probes for writeback. Creating it is a one-time step per repository.

**Step 5: Verify setup**

```bash
parity doctor
```

Checks API keys, hint pattern matches, and context file completeness. Fix any ✗ items before opening your first PR.

**Step 6: Open a PR**

Open any PR. To see Parity in action immediately, modify a file listed in your `parity.yaml` `behavior_artifacts.paths` or `guardrail_artifacts.paths` — these files are pre-loaded for efficiency. However, Parity will detect changes to **any file** if it judges them behaviorally significant. The hint patterns are optimization hints, not gates. The `parity-analyze` job runs automatically.

### `parity.yaml` Full Reference

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
# Uncomment the platform(s) you want to use. Parity reads datasets from configured platforms.
platforms:
  langsmith:
    api_key_env: LANGSMITH_API_KEY       # env var name, not the value
  # braintrust:
  #   api_key_env: BRAINTRUST_API_KEY
  #   org: "my-org"
  # arize_phoenix:
  #   api_key_env: PHOENIX_API_KEY
  #   base_url: "https://app.phoenix.arize.com"
  # promptfoo:
  #   config_path: "promptfooconfig.yaml"  # relative to repo root

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
  model: "text-embedding-3-small"         # OpenAI only: text-embedding-3-small (1536-dim) or text-embedding-3-large (3072-dim)
  dimensions: 1536                        # (optional) specify for custom embedding backends
  cache_path: ".parity/embedding_cache.db"

# ── Similarity Thresholds ────────────────────────────────────────────────────
similarity:
  duplicate_threshold: 0.88
  boundary_threshold: 0.72

# ── Probe Generation ─────────────────────────────────────────────────────────
generation:
  proposal_probe_limit: 8
  candidate_probe_pool_limit: 20
  diversity_limit_per_gap: 2

# ── Spend Caps ───────────────────────────────────────────────────────────────
spend:
  analysis_total_spend_cap_usd: 2.25
  # Advanced expert overrides:
  # stage1_agent_cap_usd: 0.7875
  # stage2_agent_cap_usd: 0.45
  # stage2_embedding_cap_usd: 0.3375
  # stage3_agent_cap_usd: 0.675

# ── Approval ─────────────────────────────────────────────────────────────────
approval:
  label: "parity:approve"

# ── Auto-Run (planned, not yet active in v1) ─────────────────────────────────
# auto_run:
#   enabled: true
#   fail_on: regression_guard
#   notify: pr_comment
```

### CLI Command Reference

#### `parity init`

Interactive setup. Scans the repository and generates `parity.yaml`, workflow file, and context pack stubs.

```bash
parity init [--context-only] [--dry-run]
```

**Flags:**
- `--context-only`: Skip yaml/workflow generation; only create context/ stub files
- `--dry-run`: Print what would be created without writing files

**Detection heuristics:**
- Scans for files matching `*prompt*.{txt,md,yaml,json,j2}`, `*instruction*`, `system_*` and files containing phrases like "you are", "your role is", "always", "never"
- Detects guardrail artifacts in paths containing `judge`, `validator`, `classifier`, etc.
- Python variable patterns: `*_prompt`, `*_instruction`, `system_*`

**Outputs:** `parity.yaml`, `.github/workflows/parity.yml`, `context/` directory with stub files

**Return codes:** 0 success, 1 user cancelled, 2 write permission error

---

#### `parity doctor`

Validates setup and reports check results. Informational only — always exits 0.

```bash
parity doctor [--config parity.yaml] [--ci]
```

**Checks:**
1. `parity.yaml` exists and is valid
2. `ANTHROPIC_API_KEY` env var is set
3. Platform-specific API keys configured
4. `OPENAI_API_KEY` set (if mappings are configured)
5. Hint patterns match at least one tracked file each
6. Key context files exist and are non-empty
7. `--ci` flag: verifies `parity:approve` label exists in GitHub

**Return codes:** Always 0 (use stderr for diagnostics)

---

#### `parity run-stage`

Primary orchestration command. Constructs and invokes the Agent SDK session for a given stage.

```bash
parity run-stage <1|2|3> \
  [--pr-number <number>]        # required for stage 1
  [--base-branch <branch>]      # required for stage 1
  [--manifest <path>]           # required for stages 2, 3
  [--gaps <path>]               # required for stage 3
  --output <path>               # output JSON file
  [--config parity.yaml]
```

**What it does:**
1. Validates required inputs
2. Loads context pack
3. Generates MCP server config
4. Invokes Agent SDK session
5. Validates output against stage schema
6. Writes metadata.json with cost, duration, model used

**Return codes:**
| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Agent SDK error |
| 2 | Output JSON validation failed |
| 3 | Budget exceeded |
| 4 | Stage-specific failure |
| 5 | Missing required inputs |

---

#### `parity post-comment`

Posts the PR comment with probe proposal or "no changes" message.

```bash
parity post-comment \
  --proposal <path> \                # OR --no-changes
  --pr-number <number> \
  [--repo <owner/repo>]             # defaults to $GITHUB_REPOSITORY
  [--token <token>]                 # defaults to $GITHUB_TOKEN
```

**Return codes:** 0 success, 1 GitHub API error, 2 invalid proposal JSON

---

#### `parity write-probes` (Stage 4)

Deterministic write command. Reads approved probe proposal and writes to eval platforms.

```bash
parity write-probes \
  --proposal <path> \
  [--config parity.yaml]
```

**Return codes:** 0 all writes succeeded, 1 partial failure (some probes written), 2 complete failure

---

#### `parity resolve-run-id` (Stage 4 helper)

Locates the earlier analysis run for a given PR head SHA to download the correct artifact.

```bash
parity resolve-run-id \
  --head-sha <sha> \
  [--repo <owner/repo>] \
  [--workflow-id parity.yml] \
  [--branch <branch>] \
  [--event pull_request] \
  [--status completed] \
  [--conclusion success] \
  [--token-env GITHUB_TOKEN]
```

**Return codes:** 0 run ID written to stdout, 1 no matching run found or GitHub API error, 2 missing required inputs

---

#### `parity get-behavior-diff`

Deterministic diff extraction. Invoked by the orchestrator before Stage 1 begins.

```bash
parity get-behavior-diff \
  --base-branch <branch> \
  --pr-number <number> \
  [--config parity.yaml]
```

**Output:** `RawChangeData` JSON to stdout (see Spec §4 RawChangeData schema)

**Return codes:** 0 success, 1 git error, 2 event payload error, 3 config error

---

#### `parity embed-batch`

Batch embedding tool. Embeds eval inputs and caches results using OpenAI embeddings API.

```bash
parity embed-batch \
  --inputs <inputs.json> \
  --output <embeddings.json> \
  --model text-embedding-3-small \
  --cache .parity/embedding_cache.db
```

**Return codes:** 0 success (including cache degradation with stderr warning), 1 OpenAI API error

---

#### `parity find-similar`

Similarity classification tool. Compares probe candidates against existing eval corpus.

```bash
parity find-similar \
  --candidate <candidate.json> \
  --corpus <embeddings.json> \
  --output <similarity.json> \
  --duplicate-threshold 0.88 \
  --boundary-threshold 0.72
```

**Classification:**
- `duplicate` (similarity ≥ 0.88)
- `boundary` (similarity 0.72–0.87)
- `related` (similarity 0.50–0.71)
- `novel` (similarity < 0.50)

**Return codes:** 0 success, 1 embedding error

---

#### `parity setup-mcp` (optional)

Generate `.claude/mcp_servers.json` from config and environment variables. Used for local debugging only; the CI analyzer path does not generate or consume this file.

```bash
parity setup-mcp [--config parity.yaml] [--output .claude/mcp_servers.json]
```

**Return codes:** 0 success (even if no servers configured), 1 config parse error

---

## 14. Data Models and Schemas

All data models use **Pydantic v2** (`BaseModel`) for runtime validation, JSON schema generation, and serialization (see DECISIONS.md). Models use `model_validate()` to instantiate from JSON and `model_dump()` to serialize.

### `EvalCase` (unified model across all platforms)

```python
class EvalCase(BaseModel):
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
class CoverageSummary(BaseModel):
    total_relevant_cases: int
    cases_covering_changed_behavior: int
    coverage_ratio: float
    platform: Optional[str]
    dataset: Optional[str]
    mode: Literal["coverage_aware", "bootstrap"]
    corpus_status: Literal["available", "empty", "unavailable"]
    retrieval_notes: Optional[str]    # optional explanatory note for non-standard retrieval paths
    bootstrap_reason: Optional[str]   # required when mode == "bootstrap"
```

### `ProbeCase`

```python
class ProbeCase(BaseModel):
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
# Generated by parity v{version} — commit {sha} — {timestamp}
# Artifact: {artifact_path}
# Review before committing to eval suite.

description: "Parity probes for {artifact_path} (PR #{pr_number})"

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
| Stage 1 Agent SDK timeout | Post comment: "Parity analysis timed out. No probes generated." Exit 0 (non-blocking). |
| Stage 1 produces no changes | Post a minimal no-changes comment ("This PR does not modify any behavior-defining artifacts"). Exit 0. |
| Stage 2 MCP connection failure | Continue with file-only fallback. Post warning in PR comment: "Could not connect to {platform}. Coverage analysis skipped; probes generated without coverage context." |
| Stage 2 no dataset mapping | Post comment with specific mapping instructions. Stage 3 still runs without coverage context. |
| Stage 2 mapped dataset exists but contains zero evals | Switch to bootstrap mode. Post warning in PR comment: "No existing eval cases were found. Probes were generated as bootstrap coverage from the diff and product context." |
| Stage 2 no eval corpus exists at all | Switch to bootstrap mode. Post warning in PR comment: "Running in bootstrap mode — probes are grounded in your diff and product context. Add eval dataset mappings to unlock coverage-aware analysis." |
| Stage 3 all probes filtered as duplicates | Post comment: "All generated probes were too similar to existing evals. No new coverage gaps identified." |
| Stage 3 produces < 3 probes after filtering | Post whatever was generated. No minimum enforcement. |
| Stage 4 write failure | Post comment on merged PR: "Probe write failed: {error}. Probes available at {artifact_path}." Exit non-zero to flag the failure. |
| Anthropic API rate limit | Retry with exponential backoff × 3, then fail gracefully as above. |

### Spend Caps and Agent SDK Budget Handling

Parity exposes one optional total analysis spend cap, then derives stage-specific caps from it:

- Stage 1 agent spend
- Stage 2 agent spend
- Stage 2 embedding spend
- Stage 3 agent spend

The agent-stage caps are passed into the Agent SDK as `max_budget_usd`. The Stage 2 embedding cap is enforced in the host-owned MCP toolbox rather than by the Agent SDK.

The Agent SDK reports spend-cap conditions distinctly:

- **`error_max_budget_usd`** (not an error flag) — The agent-stage spend ceiling hit before completion. Partial work may have been done; parity attempts partial result extraction and, for Stage 2, falls back to a degraded valid manifest.
- **`is_error == True`** — Genuine execution failure (MCP connection dropped, invalid response, etc.).
- **Rate limit errors** — Surface as `AssistantMessage.error == "rate_limit"`. Parity retries up to 3 times with exponential backoff (30s, 60s, 120s).

**Default spend split from `analysis_total_spend_cap_usd = 2.25`:**

| Spend bucket | Default cap | Justification |
|---|---|---|
| Stage 1 agent | 0.7875 | Open-ended codebase discovery: reads changed files, analyzes diffs, and may fetch additional files via tools. |
| Stage 2 agent | 0.45 | Coverage analysis reasoning over mappings, retrieval outcomes, and similarity results. |
| Stage 2 embedding | 0.3375 | Host-owned OpenAI embedding spend for coverage comparison. |
| Stage 3 agent | 0.675 | Pure probe generation from curated context; no tool traversal. |

These are configurable in `parity.yaml` under `spend:`. Most users should only set `analysis_total_spend_cap_usd`; stage-specific overrides are expert-only.

### Complete Error Handling Table

| Failure | Subtype / Condition | PR Comment Behaviour | Exit Code |
|---|---|---|---|
| Stage 1 spend cap exceeded | `error_max_budget_usd` | "Parity analysis exceeded the Stage 1 spend cap. No probes generated." | 3 |
| Stage 1 SDK crash | `is_error == True` | "Parity failed during change analysis. See Actions log." | 1 |
| Stage 1 git error | `get-behavior-diff` returns 1; run-stage wraps as no changes | Silent exit (no comment, no probes) | 0 |
| Stage 2 agent spend cap exceeded | `error_max_budget_usd` | Warning + degraded valid manifest derived from Stage 1 and host-side retrieval state | 0 (Stage 3 continues) |
| Stage 2 embedding spend cap exceeded | `embed_batch.budget_exceeded == true` | No hard failure; Stage 2 degrades to partial/bootstrap analysis and continues | 0 (Stage 3 continues) |
| Stage 2 MCP connection failure | `AssistantMessage.error` populated | Warning in PR comment: "Could not connect to {platform}. Coverage analysis skipped; probes generated without coverage context." | 0 (continue to Stage 3) |
| Stage 3 spend cap exceeded | `error_max_budget_usd` | "Probe generation exceeded the Stage 3 spend cap. Partial probes (if any) shown below." | 3 |
| Stage 3 all probes filtered | Empty probes array after similarity filtering | "All generated probes were too similar to existing coverage. No new gaps identified." | 0 |
| Stage 3 < 3 probes generated | After filtering, fewer than 3 probes remain | Post whatever was generated; no minimum enforcement. | 0 |
| Stage 4 write failure | Platform SDK exception | Post to merged PR: "Probe write failed: {error}. Probes available at {artifact_path}." | 1 |
| Rate limit (any stage) | `AssistantMessage.error == "rate_limit"` | Retry automatically × 3 with exponential backoff. After 3 failures, treat as spend cap exhaustion. | 3 |
| GitHub API error (posting PR comment) | `httpx.HTTPStatusError` | Log to stderr. Do NOT fail the run — artifact is still uploaded to Actions. | 0 |

### Missing Dataset Mapping Warning

When Stage 2 finds no mapping for a changed artifact:

```markdown
⚠️ **No eval dataset mapped for `prompts/citation_agent/system_prompt.md`**

Coverage analysis was skipped for this artifact. Probes were generated without 
existing coverage context, which may reduce their relevance.

To add a mapping, add the following to `parity.yaml`:

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

Parity is running without product context, user profiles, or interaction patterns.
Probe quality will be significantly reduced — probes may not reflect your product's 
actual users or vocabulary.

Run `parity init --context-only` to create a context pack.
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

**DeepEval integration** — DeepEval test cases are defined in Python code, not a structured data file. No clean programmatic read path exists. v1 exports parity-generated probes as DeepEval Python stubs (in `.parity/runs/{sha}/probes_deepeval.py`) but does not read existing DeepEval cases for coverage analysis. Teams using DeepEval should add a `deepeval_cases.json` export step to CI and point parity at that file.

**Prompts stored in databases** — No reliable way to detect or retrieve runtime-fetched prompts without application-level hooks. If a prompt is fetched from a database at runtime and not tracked as a file, parity will not detect changes to it. Documented workaround: add a `parity export` call to the application that dumps current prompt state to a tracked file.

**Cross-artifact interaction effect analysis** — Analysis of how a change to a system prompt interacts with a concurrently unchanged tool description or retrieval instruction. Stage 1 analyzes changed artifacts; it does not reason about interactions with unchanged artifacts. Compound changes (both changed in the same PR) are detected and flagged; cross-artifact interactions where only one changed are deferred.

**GitHub App / PR comment command approval** — The `/parity approve 1,3,5` comment command pattern requires a webhook server or GitHub App infrastructure. Deferred to v2. v1 uses label-only approval.

**Humanloop** — Platform sunsetted September 8, 2025. Not supported.

**Cost tracking dashboard** — Per-PR and per-repo cost tracking for Anthropic API usage. Stage costs are logged to `metadata.json` per run. Aggregation and dashboard deferred.

---

## Appendix A: Quick Reference

### CLI Commands at a Glance

| Command | Purpose | Key Flags |
|---------|---------|-----------|
| `parity init` | Interactive setup; detect artifacts, create config/workflow/context stubs | `--context-only`, `--dry-run` |
| `parity doctor` | Validate setup (config, API keys, patterns, context files) | `--ci` (check GitHub label) |
| `parity run-stage <1\|2\|3>` | Execute a pipeline stage; output structured JSON | `--pr-number`, `--base-branch`, `--manifest`, `--gaps`, `--output`, `--config` |
| `parity get-behavior-diff` | Extract and structure PR changes (internal; called by run-stage) | `--base-branch`, `--pr-number`, `--config` |
| `parity embed-batch` | Batch embed eval inputs; cache results (internal helper; also available for debugging) | `--inputs`, `--output`, `--model`, `--cache` |
| `parity find-similar` | Classify one candidate against existing eval corpus (internal helper; also available for debugging) | `--candidate`, `--corpus`, `--output`, `--duplicate-threshold`, `--boundary-threshold` |
| `parity find-similar-batch` | Classify a scoped batch of candidates against one embedded corpus | `--candidates`, `--corpus`, `--output`, `--duplicate-threshold`, `--boundary-threshold` |
| `parity post-comment` | Post/update PR comment with probe proposal or "no changes" message | `--proposal` or `--no-changes`, `--pr-number`, `--repo`, `--token` |
| `parity write-probes` | Write approved probes to eval platform (Stage 4) | `--proposal`, `--config`, `--outcome-output`, `--skip-comment` |
| `parity post-write-comment` | Post merged-PR writeback results from a saved outcome file | `--outcome`, `--pr-number`, `--repo`, `--token`, `--run-id` |
| `parity resolve-run-id` | Locate analysis run by head SHA for artifact download (Stage 4 helper) | `--head-sha`, `--repo`, `--workflow-id`, `--branch`, `--status`, `--conclusion` |
| `parity setup-mcp` | Generate MCP server config from parity.yaml and env vars (optional; for local debugging) | `--config`, `--output` |

### Exit Codes

| Code | Stage(s) | Meaning |
|------|----------|---------|
| **0** | All | Success (or non-blocking warning) |
| **1** | 1, 2, 3, 4 | Agent SDK error, MCP connection failure, API error, partial write failure |
| **2** | 2, 3 | Output JSON failed schema validation; invalid proposal JSON (write-probes) |
| **3** | 1, 3 | Agent spend cap exceeded (`error_max_budget_usd`) |
| **4** | 1 | Stage-specific failure (e.g., git error, config parsing) |
| **5** | 1, 2, 3 | Missing required inputs (e.g., `--pr-number` for Stage 1) |

### Key Configuration Sections

| Section | Location | Purpose |
|---------|----------|---------|
| `behavior_artifacts` | `parity.yaml` | Hint patterns for behavior-defining files (prompts, instructions, configs) |
| `guardrail_artifacts` | `parity.yaml` | Hint patterns for guardrail artifacts (judges, validators, classifiers) |
| `context` | `parity.yaml` | Paths to product context, user profiles, examples, and traces |
| `platforms` | `parity.yaml` | Enabled eval platforms (LangSmith, Braintrust, Arize Phoenix, Promptfoo) |
| `mappings` | `parity.yaml` | Links between artifacts and eval datasets for coverage analysis |
| `embedding` | `parity.yaml` | Embedding model, dimensions, cache path (OpenAI only) |
| `similarity` | `parity.yaml` | Duplicate and boundary thresholds (default 0.88 and 0.72) |
| `generation` | `parity.yaml` | Final proposal size, candidate pool size, and diversity limits |
| `approval` | `parity.yaml` | GitHub label name for writeback approval (default: `parity:approve`) |
| `spend` | `parity.yaml` | Overall analysis spend cap plus expert-only stage-specific overrides |
| `auto_run` | `parity.yaml` | Stage 4 auto-run policy (enabled, fail_on condition, notification mode) |

### Data Model Overview

All models use **Pydantic v2** (`BaseModel`) for validation and JSON serialization. See Spec §14 for complete schemas.

| Model | Stage | Purpose |
|-------|-------|---------|
| `RawChangeData` | Stage 1 input | Git diff + PR metadata + pre-loaded hint matches |
| `BehaviorChangeManifest` | Stage 1 output | Detected changes, intent, risk flags per artifact |
| `CoverageGapManifest` | Stage 2 output | Coverage gaps, nearest existing evals, bootstrap flags |
| `ProbeProposal` | Stage 3 output | Ranked probe candidates with rationale and metadata |

### Stage Spend Defaults

Typical costs per PR run (using claude-sonnet-4):

| Stage / bucket | Default cap | Typical Cost | Max Turns |
|-------|---|---|---|
| Stage 1 agent (Change detection) | $0.7875 | $0.05–0.30 | 40 |
| Stage 2 agent (Coverage analysis) | $0.45 | $0.05–0.25 | 40 |
| Stage 2 embedding | $0.3375 | $0.01–0.20 | — |
| Stage 3 agent (Probe generation) | $0.675 | $0.10–0.60 | 25 |
| **Total** | **$2.25** | **$0.25–1.40** | — |

Increase `spend.analysis_total_spend_cap_usd` if runs consistently exhaust spend on large diffs or broad eval corpora. Use stage-specific overrides only if you need expert-level control.

### Error Classification Quick Lookup

**No changes detected (non-failure):**
- Stage 1 `get-behavior-diff` returns error → treated as no changes, exit 0

**Warnings (non-blocking):**
- Stage 2 eval retrieval failed → continue without coverage context
- Stage 2 dataset mapping missing → continue in starter mode
- Stage 2 dataset exists but empty → continue in starter mode

**Failures (stop processing):**
- Budget exceeded (`error_max_budget_usd`) → exit 3, optional partial result
- Agent SDK error (`is_error == True`) → exit 1
- Schema validation failed → exit 2
- Missing inputs → exit 5

**Rate limits (auto-retry):**
- `AssistantMessage.error == "rate_limit"` → retry up to 3× with exponential backoff (30s, 60s, 120s)

### GitHub Actions Integration

**Workflow triggers:**
- `pull_request: types: [opened, synchronize, reopened]` — Stages 1–3 analysis (Stages 1–3 runs as `parity-analyze` job)
- `pull_request_target: types: [closed]` — Stage 4 write (triggered by `parity:approve` label + merge)

**Secrets required:**
- `ANTHROPIC_API_KEY` (always required)
- `OPENAI_API_KEY` (for coverage-aware mode)
- `LANGSMITH_API_KEY`, `BRAINTRUST_API_KEY`, `PHOENIX_API_KEY` (if using platform integration)
- `GITHUB_TOKEN` (auto-provided)

---

**End of Specification**
