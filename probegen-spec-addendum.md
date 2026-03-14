# Probegen Spec Addendum — Implementation Gaps

This document is an addendum to `probegen-spec.md`. It fills the six gaps identified as blocking implementation. All technical details are verified against primary documentation.

---

## Gap 1: `RawChangeData` Schema — `get_behavior_diff` Tool I/O Contract

The `get_behavior_diff` tool is a Python CLI entry point shipped with the probegen package. It wraps three data sources and returns a single structured JSON object written to stdout.

### Invocation (from Agent SDK Bash tool)

```bash
probegen get-behavior-diff \
  --base-branch main \
  --pr-number 142 \
  --config probegen.yaml
```

### Input Sources

**Source 1 — Git diff**
```bash
git diff origin/{base_branch}...HEAD -- {behavior_artifact_paths} {guardrail_artifact_paths}
```
Run with `--unified=5` (5 lines of context). Full file content for changed files is fetched separately via `git show HEAD:{path}` and `git show origin/{base}:{path}`.

**Source 2 — GitHub event payload**
Read from `$GITHUB_EVENT_PATH`. The `pull_request` webhook payload (confirmed schema post-October 2025 changes) provides:
```
pull_request.number
pull_request.title
pull_request.body
pull_request.base.ref
pull_request.head.sha
pull_request.labels[].name
pull_request.user.login
repository.full_name
```
Note: `author_association` was removed from the PR payload in October 2025. Do not reference it.

**Source 3 — Full artifact content**
For each changed file: full before and after content via `git show`. Not just the diff lines.

### Output: `RawChangeData` Schema

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
  "changed_artifacts": [
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
  "unchanged_behavior_artifacts": [
    "prompts/planner/planner_prompt.md"
  ],
  "has_changes": true,
  "artifact_count": 1
}
```

### Return Codes

| Code | Meaning |
|---|---|
| 0 | Success, JSON written to stdout |
| 1 | Git error (no history, bad base branch) |
| 2 | `GITHUB_EVENT_PATH` not set or malformed |
| 3 | `probegen.yaml` not found or invalid |

When `has_changes` is false, the JSON is still valid and complete — the `changed_artifacts` array is empty. The stage runner reads `has_changes` to determine gate behaviour; it does not treat an empty array as an error.

---

## Gap 2: Prompt Rendering Contract

Each stage prompt is a Python function in `probegen/prompts/` that takes structured inputs and returns a fully assembled string. The assembly rules are:

### Template Format

Plain f-string templates. No Jinja, no external template engine. Each stage has one `render_stage{N}_prompt()` function. The function is responsible for all truncation.

### Token Budget Per Context Section (Stage 3)

The Context Pack is the largest variable input. Total context budget for Stage 3 is 80,000 tokens (well within claude-sonnet-4's 200k context window, leaving headroom for the agent's own tool calls and response). Allocation:

| Section | Token Budget | Truncation Strategy |
|---|---|---|
| System prompt template | ~3,000 | Fixed |
| Stage 1 stripped manifest | ~2,000 | Fixed (structured JSON, already small) |
| Stage 2 gap manifest | ~3,000 | Fixed (structured JSON) |
| `product.md` | 4,000 | Hard truncate at limit, append `[truncated]` |
| `users.md` | 2,000 | Hard truncate |
| `interactions.md` | 3,000 | Hard truncate |
| `good_examples.md` | 3,000 | Hard truncate |
| `bad_examples.md` | 4,000 | Hard truncate — this is the highest-value section |
| Trace samples | 6,000 | Random sample up to `trace_max_samples`, then truncate each trace to 300 tokens |
| Nearest existing cases (from Stage 2) | 4,000 | Top 5 per gap, each truncated to 200 tokens |

Token counting uses `tiktoken` with `cl100k_base` encoding (compatible with Claude token counts at this resolution). Truncation is applied per-section before assembly. If total assembled prompt exceeds 80,000 tokens, reduce trace sample count first, then good/bad examples.

### Stage 1 Prompt Rendering

```python
def render_stage1_prompt(raw_change_data: dict, context: ContextPack) -> str:
    return STAGE1_SYSTEM_TEMPLATE.format(
        product_context=truncate(context.product, 4000),
        bad_examples=truncate(context.bad_examples, 4000),
        raw_change_data_json=json.dumps(raw_change_data, indent=2),
    )
```

Stage 1 does NOT receive: users.md, interactions.md, good_examples.md, traces. Only product context and bad examples are relevant for intent analysis.

### Stage 2 Prompt Rendering

```python
def render_stage2_prompt(stage1_manifest: dict) -> str:
    # Strip raw_diff from each changed artifact before injection
    stripped = strip_raw_diffs(stage1_manifest)
    return STAGE2_SYSTEM_TEMPLATE.format(
        manifest_json=json.dumps(stripped, indent=2),
    )
```

Stage 2 receives NO context pack. It receives only the stripped manifest. The MCP tools provide eval corpus data dynamically.

If Stage 2 retrieves zero relevant eval cases, it still emits a valid `CoverageGapManifest` in bootstrap mode with:
- `coverage_summary.mode = "bootstrap"`
- `coverage_summary.corpus_status = "empty"` or `"unavailable"`
- `coverage_summary.bootstrap_reason` explaining why corpus comparison was not possible
- empty `nearest_existing_cases` arrays on bootstrap gaps

### Stage 3 Prompt Rendering

```python
def render_stage3_prompt(
    stage1_manifest: dict,
    stage2_manifest: dict,
    context: ContextPack,
) -> str:
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
        max_probes_surfaced=config.generation.max_probes_surfaced,
    )
```

`extract_stage1_brief` returns only: `inferred_intent`, `unintended_risk_flags`, `affected_components`, `overall_risk`, `compound_change_detected` per artifact. Raw diffs, before/after content, PR metadata are stripped.

---

## Gap 3: `embed_batch` and `find_similar` Tool I/O Contracts

Both tools are Python CLI entry points in the probegen package. They communicate via JSON files (not stdout) because embeddings are large binary-adjacent data not suited to stdout piping.

### `embed_batch`

**Invocation:**
```bash
probegen embed-batch \
  --inputs /tmp/inputs.json \
  --output /tmp/embeddings.json \
  --model text-embedding-3-small \
  --cache .probegen/embedding_cache.db
```

**Input file schema (`inputs.json`):**
```json
[
  {
    "id": "case_a3f2",
    "text": "What year was the Paris Agreement signed?"
  },
  {
    "id": "case_b17d",
    "text": "SYSTEM: You are a helpful assistant.\nUSER: Tell me about climate policy"
  }
]
```

`id` is the `EvalCase.id` or a generated UUID for probe candidates. `text` is the `input_normalized` string — already normalised before this call.

**Output file schema (`embeddings.json`):**
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

The OpenAI embeddings API response shape (`data[].embedding` as a list of floats) is unwrapped before writing. `dimensions` for `text-embedding-3-small` is 1536 by default. If the `dimensions` parameter is specified in config, that value is used (the API supports dimension reduction for this model family).

**Cache behaviour:** For each input, compute `sha256(id + text)`. Check cache by `(id, text_hash, model)`. On cache hit, set `cached: true` and skip the API call. Batch non-cached inputs into a single API call (OpenAI embeddings API accepts array input).

**Return codes:** 0 success, 1 API error, 2 cache error (non-fatal — continues without cache).

---

### `find_similar`

**Invocation:**
```bash
probegen find-similar \
  --candidate /tmp/candidate.json \
  --corpus /tmp/embeddings.json \
  --output /tmp/similarity.json \
  --duplicate-threshold 0.88 \
  --boundary-threshold 0.72
```

**Candidate file schema (`candidate.json`):**
```json
{
  "id": "probe_001_candidate",
  "text": "So what do you think about climate policy generally?"
}
```

The candidate is embedded inline by `find_similar` (no separate embed step required for single candidates — it calls the embedding API directly, or uses the cache if the text_hash matches).

**Output file schema (`similarity.json`):**
```json
{
  "candidate_id": "probe_001_candidate",
  "results": [
    {
      "corpus_id": "case_a3f2",
      "similarity": 0.74,
      "classification": "boundary"
    },
    {
      "corpus_id": "case_b17d",
      "similarity": 0.43,
      "classification": "novel"
    }
  ],
  "top_match": {
    "corpus_id": "case_a3f2",
    "similarity": 0.74,
    "classification": "boundary"
  },
  "max_similarity": 0.74,
  "overall_classification": "boundary"
}
```

`overall_classification` is determined by `top_match.classification`. The full `results` array is sorted descending by similarity. `classification` values: `duplicate` (≥0.88), `boundary` (0.72–0.87), `related` (0.50–0.71), `novel` (<0.50). Thresholds are passed as CLI args, defaulting to the `probegen.yaml` values.

---

## Gap 4: `probegen` CLI Interface

Full specification of every command, flag, return code, and output target.

### Command Structure

```
probegen <command> [options]
```

### Commands

---

#### `probegen init`

Interactive setup. Scans the repository and generates `probegen.yaml` and workflow file.

```
probegen init [--context-only] [--dry-run]
```

**Flags:**
- `--context-only`: Skip yaml/workflow generation; only create `context/` stub files
- `--dry-run`: Print what would be created without writing files

**Codebase scanning heuristics (for `behavior_artifacts.paths` detection):**

Scans the repo root and all subdirectories (excluding `.git`, `node_modules`, `__pycache__`, `venv`, `.venv`) for:

1. Files matching `*.md` containing any of: `you are`, `your role is`, `your task is`, `always`, `never`, `when the user`, `respond with` (case-insensitive) in the first 500 chars
2. Files matching `*prompt*.{txt,md,yaml,json,j2}` or `*instruction*.{txt,md,yaml}` or `*system*.{txt,md,yaml}`
3. Python files containing string assignments to variables matching `*_prompt`, `*_instruction`, `system_*`, `*_template` (via AST walk, top-level and class-level assignments only)
4. YAML/JSON files with keys matching: `system_prompt`, `instructions`, `tool_description`, `planner_prompt`, `retrieval_instruction`

**Guardrail artifact detection:**
Files in paths containing `judge`, `validator`, `guardrail`, `classifier`, `filter`, `rubric`, `safety` (path component, case-insensitive). Python files with class names or function names matching these patterns.

**Interactive question sequence:**
```
1. Detected these likely behavior-defining artifacts:
   - prompts/citation_agent/system_prompt.md
   - agents/planner/planner.py (contains: planner_prompt)
   Are these correct? [Y/n/edit]

2. Detected these likely guardrail artifacts:
   - judges/citation_quality.md
   Are these correct? [Y/n/edit]

3. Which eval platform do you use?
   [1] LangSmith  [2] Braintrust  [3] Arize Phoenix  [4] Promptfoo  [5] None / file export

4. For artifact 'prompts/citation_agent/system_prompt.md':
   Which dataset contains existing evals for this artifact?
   (Leave blank if you are starting without evals — probegen will run in bootstrap mode)
   Dataset name: _

5. Create a context/ directory with stub files? [Y/n]
```

**Outputs:**
- `probegen.yaml` at repo root
- `.github/workflows/probegen.yml`
- `context/` directory with stub files (if accepted)

**Return codes:** 0 success, 1 user cancelled, 2 write permission error.

---

#### `probegen setup-mcp`

Generate `.claude/mcp_servers.json` from `probegen.yaml` and environment variables.

```
probegen setup-mcp [--config probegen.yaml] [--output .claude/mcp_servers.json]
```

Reads API keys from environment. Writes only servers for which a valid key is present. Silently skips unconfigured platforms — no error.

**Return codes:** 0 success (even if no servers configured), 1 config parse error.

---

#### `probegen get-behavior-diff`

Deterministic diff extraction tool. Called by the Agent SDK as a Bash tool in Stage 1.

```
probegen get-behavior-diff \
  --base-branch <branch> \
  --pr-number <number> \
  [--config probegen.yaml]
```

Writes `RawChangeData` JSON to stdout. See Gap 1 for full schema.

**Return codes:** 0 success, 1 git error, 2 event payload error, 3 config error.

---

#### `probegen embed-batch`

See Gap 3 for full specification.

**Return codes:** 0 success, 1 OpenAI API error (fatal), 2 cache error (non-fatal, continues).

---

#### `probegen find-similar`

See Gap 3 for full specification.

**Return codes:** 0 success, 1 embedding error.

---

#### `probegen run-stage`

Primary orchestration command. Constructs and invokes the Agent SDK session for a given stage.

```
probegen run-stage <1|2|3> \
  [--pr-number <number>]        # required for stage 1
  [--base-branch <branch>]      # required for stage 1
  [--manifest <path>]           # required for stages 2, 3 (stage1 output)
  [--gaps <path>]               # required for stage 3 (stage2 output)
  --output <path>               # where to write stage JSON output
  [--config probegen.yaml]
```

**What `run-stage` does:**

1. Validates required inputs are present
2. Loads context pack from paths in `probegen.yaml`
3. Renders the stage prompt using the appropriate `render_stageN_prompt()` function
4. Runs `probegen setup-mcp` to refresh `.claude/mcp_servers.json`
5. Invokes the Agent SDK `query()` with the rendered prompt and stage-specific options
6. Streams messages; on each `AssistantMessage`, writes progress to stderr
7. On `ResultMessage`:
   - If `subtype == "success"`: parse result JSON, validate against stage schema, write to `--output`
   - If `subtype == "error_max_budget_usd"`: follow budget-exceeded failure path (see Gap 8)
   - If `is_error == True`: follow generic failure path
8. Writes `metadata.json` alongside output with: `{stage, model, cost_usd, duration_ms, num_turns, timestamp}`

**Return codes:**

| Code | Meaning |
|---|---|
| 0 | Success, output JSON written |
| 1 | Agent SDK error |
| 2 | Output JSON failed schema validation |
| 3 | Budget exceeded (`error_max_budget_usd`) |
| 4 | Stage-specific failure (e.g. stage 1 git error) |
| 5 | Missing required inputs |

The GitHub Action checks `run-stage` exit code. Any non-zero code from stage 1 results in a silent exit (no PR comment). Non-zero from stages 2 or 3 results in a partial PR comment with a warning (see Gap 8).

---

#### `probegen post-comment`

Posts the PR comment from a `ProbeProposal.json`. Handles create vs. update logic.

```
probegen post-comment \
  --proposal <path> \
  --pr-number <number> \
  [--repo <owner/repo>]         # defaults to $GITHUB_REPOSITORY
  [--token <token>]             # defaults to $GITHUB_TOKEN
```

See Gap 7 for full comment posting logic.

**Return codes:** 0 success, 1 GitHub API error, 2 invalid proposal JSON.

---

#### `probegen write-probes` (Stage 4)

Deterministic write command. No Agent SDK involved.

```
python -m probegen.write_probes \
  --proposal <path>
  [--config probegen.yaml]
```

See spec §9 for platform write implementations. Uses environment variables for API keys (same as analysis stages).

**Return codes:** 0 all writes succeeded, 1 partial failure (some probes written, some not — logged), 2 complete failure.

---

## Gap 5: `probegen init` — Full Stub File Contents

### `context/product.md` (generated stub)

```markdown
# Product Context

## What This Product Does
<!-- Describe the product in 2-3 sentences. What problem does it solve? -->

## Who Uses It
<!-- Describe the primary user types. Are they technical? Non-technical? What is their domain? -->

## The Agent's Role
<!-- What does the LLM agent do within this product? What decisions does it make? -->

## Stakes and Sensitivity
<!-- How consequential are mistakes? Are there compliance, legal, or safety implications? -->

## Domain Vocabulary
<!-- List any domain-specific terms, abbreviations, or jargon the agent uses or encounters. -->
```

### `context/users.md` (generated stub)

```markdown
# User Profiles

## Primary User Types
<!-- For each user type, describe: who they are, their technical level, their goals, their frustrations. -->

### [User Type 1]
- **Who:** 
- **Technical level:** 
- **Primary goals:** 
- **Common frustrations:** 
- **How they phrase requests:** 

## Vocabulary Notes
<!-- How do users in this domain actually phrase things? Formal or casual? Terse or verbose? -->
```

### `context/interactions.md` (generated stub)

```markdown
# Interaction Patterns

## Common Flows
<!-- Describe the 3-5 most common user interaction sequences. What does a typical session look like? -->

### Flow 1: [Name]
1. User initiates with: 
2. Agent responds with: 
3. User follow-up: 

## Multi-Turn Patterns
<!-- If the agent is conversational, what does a typical conversation arc look like? -->

## What Users Expect
<!-- What do users assume the agent can or cannot do? What surprises them? -->
```

### `context/good_examples.md` (generated stub)

```markdown
# What Good Looks Like

## Example 1: [Scenario name]
**Input:**
```
[example user input]
```
**Expected output characteristics:**
- [What the response should include]
- [Tone and register]
- [Format requirements]

## Example 2: [Scenario name]
<!-- Repeat for each major use case -->

## Common Patterns in Good Responses
<!-- What do all good responses have in common? -->
```

### `context/bad_examples.md` (generated stub)

```markdown
# Known Failure Modes

## Failure 1: [Name]
**What happens:** 
**Example input that triggers it:**
```
[input]
```
**What the agent incorrectly does:**
**What it should do instead:**
**Date first observed / ticket reference:** 

## Failure 2: [Name]
<!-- Repeat for each known failure -->

## Systemic Patterns
<!-- Are there categories of failures that recur? What causes them? -->

## Edge Cases to Watch
<!-- Inputs that are near the boundary of the agent's capabilities or instructions -->
```

### `context/traces/` directory

The stub creation creates an empty directory with a `README.md`:

```markdown
# Production Traces

Place anonymised production conversation samples here as .txt or .json files.

**Important:** Ensure all traces are anonymised before committing. Remove names, email 
addresses, account IDs, and any other personally identifiable information.

## .txt format
One conversation per file. Format:
```
USER: [message]
ASSISTANT: [response]
USER: [follow-up]
ASSISTANT: [response]
```

## .json format
Array of message objects:
```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```
```

---

## Gap 6: Multi-Turn Probe Format

### ProbeCase `input` for Conversational Probes

When `is_conversational: true`, the `input` field is a list of message objects matching the OpenAI/Anthropic conversation format:

```json
{
  "probe_id": "probe_004",
  "probe_type": "boundary_probe",
  "is_conversational": true,
  "input": [
    {
      "role": "user",
      "content": "I've been looking at some research on climate change."
    },
    {
      "role": "assistant",
      "content": "That's a rich area of research. What aspects are you exploring?"
    },
    {
      "role": "user",
      "content": "Just curious really — what do you think about it generally?"
    }
  ],
  "input_format": "conversation",
  "expected_behavior": "Agent responds conversationally to the final user message without inserting a citation",
  "expected_behavior_type": "llm_rubric",
  "rubric": "The response to the final user message ('Just curious really — what do you think about it generally?') does NOT include a citation, footnote, source reference, or 'according to [source]' phrasing. The response is conversational in register, appropriate for a casual opinion-seeking exchange."
}
```

The `input` list represents the full conversation history. The **last message** with `role: "user"` is the test stimulus. All prior messages are context.

### Promptfoo Export for Conversational Probes

Promptfoo supports multi-turn inputs via the `messages` var with a conversation array. The probe exports as:

```yaml
  - description: "[boundary_probe] probe_004 — No citation in casual conversational follow-up"
    # Gap: Citation rule not scoped to register
    # is_conversational: true
    vars:
      messages:
        - role: user
          content: "I've been looking at some research on climate change."
        - role: assistant
          content: "That's a rich area of research. What aspects are you exploring?"
        - role: user
          content: "Just curious really — what do you think about it generally?"
    assert:
      - type: llm-rubric
        value: >
          The response to the final user message does NOT include a citation,
          footnote, source reference, or 'according to [source]' phrasing.
          The response is conversational in register.
```

The prompt template for the test file must handle the `messages` var. Probegen generates a companion prompt file `prompts/conversational_probe_prompt.json` alongside the test YAML that passes the `messages` array through to the provider:

```json
[
  {% for message in messages %}
  {
    "role": "{{ message.role }}",
    "content": {{ message.content | dump }}
  }{% if not loop.last %},{% endif %}
  {% endfor %}
]
```

### Conversational Probe Generation Guidance (Stage 3 Prompt Addendum)

The Stage 3 prompt includes this additional block when `gap.is_conversational == true`:

```
MULTI-TURN PROBE GENERATION:
This gap is for a conversational agent. Generate probe inputs as full conversation
histories, not single-turn strings.

Rules:
- The conversation must have at least 2 turns before the test stimulus
- Prior turns must be realistic for this agent and these users — use the trace samples
  and interaction patterns as your model
- The test stimulus (final user message) is where the behavioral consequence of the
  diff will manifest
- Do not make prior turns artificially simple — they should reflect realistic 
  conversation context that could plausibly precede the test stimulus
- The expected_behavior and rubric must be scoped to the final agent response only,
  not the full conversation

Conversation format: list of {"role": "user"|"assistant", "content": "..."} objects.
First message must be role: "user". Last message must be role: "user".
```

### Similarity Normalisation for Conversational Inputs

When computing similarity for conversational probe inputs, `input_normalized` concatenates all turns:

```python
def normalize_conversational(messages: list[dict]) -> str:
    return "\n".join(
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in messages
    )
```

This matches the normalisation applied to existing conversational eval cases during Stage 2 retrieval. The similarity comparison is therefore between the full conversation normalised strings, not just the final user message. This is correct behaviour — a boundary probe should be near a similar conversation, not just a similar final message.

---

## Gap 7: PR Comment Posting Implementation

### Endpoint

PR comments in GitHub are issue comments. The correct endpoint (confirmed from GitHub docs):

```
POST /repos/{owner}/{repo}/issues/{issue_number}/comments
```

PRs are issues in GitHub's data model. Using the pulls API for comments is for line-specific review comments, not general PR comments. Probegen uses issue comments.

### Create Comment

```python
import httpx

def post_pr_comment(
    pr_number: int,
    body: str,
    repo: str,
    token: str,
) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = httpx.post(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
        headers=headers,
        json={"body": body},
    )
    response.raise_for_status()
    return response.json()   # includes comment["id"] for future updates
```

### Update vs. Create on Re-runs

On PR synchronize (new commits pushed to an open PR), probegen re-runs. The behaviour:

**Find existing probegen comment:**
```python
def find_existing_comment(pr_number: int, repo: str, token: str) -> Optional[int]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Paginate through comments to find one with the probegen marker
    page = 1
    while True:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        response.raise_for_status()
        comments = response.json()
        for comment in comments:
            if "<!-- probegen-comment -->" in comment["body"]:
                return comment["id"]
        if len(comments) < 100:
            break
        page += 1
    return None
```

**Update existing comment:**
```python
def update_pr_comment(comment_id: int, body: str, repo: str, token: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    httpx.patch(
        f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}",
        headers=headers,
        json={"body": body},
    ).raise_for_status()
```

**Comment body marker:** Every probegen comment body includes `<!-- probegen-comment -->` as the first line (HTML comment, not rendered in GitHub UI). This is the identifier used for update detection.

**Behaviour on re-run:**
1. Find existing comment by marker
2. If found: update it with new proposal content, note at top: `> ⟳ Updated for commit {sha}`
3. If not found: create new comment

This means developers always see the latest probes in a single comment thread, not a new comment per push.

### Stage 4 Post-Merge Comment

Same endpoint, same pattern. GitHub allows comments on merged/closed PRs.

```python
def post_results_comment(pr_number: int, results: RunResults, repo: str, token: str):
    body = render_results_comment(results)  # see spec §9
    # Do NOT look for existing probegen-comment to update — 
    # results comment is a separate, new comment
    # Stage 4 uses a different marker: <!-- probegen-results -->
    post_pr_comment(pr_number, body, repo, token)
```

---

## Gap 8: Budget Cap Handling and Complete Error Handling

### Verified Agent SDK Behaviour

From the Agent SDK documentation (confirmed):

- `ResultMessage.subtype == "error_max_budget_usd"` when `max_budget_usd` is exceeded
- `ResultMessage.is_error` is **false** in this case — it is a limit, not an error
- `ResultMessage.subtype == "success"` even when `max_turns` is exceeded — `max_turns` does NOT produce a distinct error subtype
- Rate limit errors surface as `AssistantMessage.error` field populated (values: `rate_limit`, `authentication_failed`, `billing_error`, etc.)
- `ResultMessage.is_error == True` for genuine execution errors (MCP connection failure, etc.)

### Per-Stage Budget Caps and Justification

| Stage | `max_budget_usd` | `max_turns` | Justification |
|---|---|---|---|
| Stage 1 | 0.50 | 30 | Codebase traversal of typical repo: 10-20 file reads + diff analysis |
| Stage 2 | 0.75 | 40 | MCP calls (3-5 per platform) + embed tool calls + gap analysis |
| Stage 3 | 1.00 | 25 | Probe generation is single-pass reasoning, not iterative traversal |

These are starting values. The `probegen.yaml` exposes them as configurable:
```yaml
budgets:
  stage1_usd: 0.50
  stage2_usd: 0.75
  stage3_usd: 1.00
```

### Budget Exceeded Handling

```python
async def run_stage(stage_num: int, prompt: str, options: ClaudeAgentOptions) -> StageResult:
    result_msg = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result_msg = message

    if result_msg is None:
        raise StageError("No ResultMessage received — SDK may have crashed")

    if result_msg.subtype == "error_max_budget_usd":
        # Partial work may have been done. Attempt to extract any JSON
        # from the last AssistantMessage before failing.
        partial = attempt_partial_extraction(result_msg.result)
        raise BudgetExceededError(
            stage=stage_num,
            cost_usd=result_msg.total_cost_usd,
            partial_result=partial,
        )

    if result_msg.is_error:
        raise StageError(
            f"Stage {stage_num} failed: {result_msg.result}",
            cost_usd=result_msg.total_cost_usd,
        )

    return StageResult(
        data=parse_stage_output(result_msg.result),
        cost_usd=result_msg.total_cost_usd,
        num_turns=result_msg.num_turns,
        duration_ms=result_msg.duration_ms,
    )
```

### Complete Error Handling Table (Addendum to Spec §15)

| Failure | Subtype / Condition | PR Comment | Exit Code |
|---|---|---|---|
| Stage 1 budget exceeded | `error_max_budget_usd` | "Probegen analysis exceeded cost limit. No probes generated. Increase `budgets.stage1_usd` in probegen.yaml." | 3 |
| Stage 1 SDK crash | `is_error == True` | "Probegen failed during change analysis. See Actions log." | 1 |
| Stage 1 git error | `run-stage` exit 1 | Silent exit (treat as no changes) | 0 |
| Stage 2 budget exceeded | `error_max_budget_usd` | Warning + partial gaps if extractable, otherwise: "Coverage analysis exceeded cost limit. Probes generated without full coverage context." | 3 (non-fatal, continue to stage 3) |
| Stage 2 MCP connection failure | `AssistantMessage.error` | Warning in PR comment. Stage 3 proceeds without coverage context. | 0 (continue) |
| Stage 3 budget exceeded | `error_max_budget_usd` | "Probe generation exceeded cost limit. Partial probes (if any) shown below." | 3 |
| Stage 3 all probes filtered | Empty probes array | "All generated probes were too similar to existing coverage. No new gaps identified." | 0 |
| Stage 4 write failure | Platform SDK exception | Post to merged PR: "Probe write failed: {error}. Manual import: {artifact_path}" | 1 |
| Rate limit (any stage) | `AssistantMessage.error == "rate_limit"` | Retry up to 3× with exponential backoff (30s, 60s, 120s). After 3 failures, treat as budget exceeded. | 3 |
| GitHub API error (comment post) | `httpx.HTTPStatusError` | Log to stderr. Do not fail the run — artifact is still uploaded. | 0 |

### Rate Limit Retry Implementation

```python
import asyncio

async def run_stage_with_retry(
    stage_num: int,
    prompt: str,
    options: ClaudeAgentOptions,
    max_retries: int = 3,
) -> StageResult:
    for attempt in range(max_retries):
        try:
            return await run_stage(stage_num, prompt, options)
        except StageError as e:
            if "rate_limit" in str(e) and attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                await asyncio.sleep(wait)
                continue
            raise
    raise StageError(f"Stage {stage_num} failed after {max_retries} retries")
```
