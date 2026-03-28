# Changelog

## 2026-03-16

### Changed
- **Stage 1 rework: agent-driven discovery.** Config patterns (`behavior_artifacts.paths`, `guardrail_artifacts.paths`) are now hints, not filters. `get_behavior_diff` returns all changed files in `all_changed_files` (unfiltered). Files matching hint patterns are pre-loaded with before/after content in `hint_matched_artifacts`. The Stage 1 agent sees everything, uses Read/Bash/Glob to inspect non-pre-loaded files it judges relevant, and decides what is behaviorally significant.
- `RawChangeData` schema: renamed `changed_artifacts` → `hint_matched_artifacts`, `unchanged_behavior_artifacts` → `unchanged_hint_matches`; added `all_changed_files` (list of all changed files), `hint_patterns` (config patterns as agent guidance). `has_changes` and `artifact_count` now reflect `all_changed_files`.
- Stage 1 `max_turns` increased from 30 to 40 to accommodate discovery tool calls.
- "Bootstrap mode" renamed to "Starter mode" in all user-facing PR comment text. Internal schema value `mode: "bootstrap"` unchanged.
- Workflow: removed redundant `Generate MCP config` step (already runs inline at stage start). Added `OPENAI_API_KEY` to Stage 2 env (required for `embed-batch` in coverage-aware mode). Added `GITHUB_RUN_ID` to write-probes env.
- PR comment when no behavioral changes detected: now posts a minimal acknowledgment comment instead of posting nothing. Includes pointer to `behavior_artifacts` hint patterns.
- PR comment warnings now appear before the Analysis Mode section (previously after). Warning text uses blockquote formatting for visibility.
- Approval instruction now includes "before merging".
- Stage 4 write failure comment now includes a link to Actions artifacts when `GITHUB_RUN_ID` is set.
- README now accurately describes the full artifact scope Parity detects: prompts, instructions, guardrails, validators, tool descriptions, classifiers, retry policies, output schemas, and other agent harness artifacts.
- README platform reference corrected from "Phoenix" to "Arize Phoenix" for consistency with spec and config.
- README probe description updated to mention multi-turn conversational probe generation.
- README Setup section: all absolute `/Users/an/...` links replaced with correct relative paths.
- `parity-spec-addendum.md` Gap 2: Stage 3 token budget fallback behaviour corrected — implementation reduces `good_examples` and `bad_examples` first, then drops traces entirely (not traces first as previously documented).
- `parity-spec-addendum.md` Gap 2: Stage 2 stripping comment updated to name all three stripped fields (`raw_diff`, `before_content`, `after_content`).
- `parity-spec.md` Step 5 of the setup checklist reworded to describe artifact matching by `parity.yaml` path patterns.

### Added
- `parity doctor` command: validates setup (API keys, hint pattern file matches, context files, optional GitHub label check). Exits 0 always; informational only.
- `parity post-comment --no-changes`: posts the minimal no-changes comment for PRs without behavioral artifact changes.
- `OPENAI_API_KEY` added to README secrets table and setup instructions.
- Label creation instruction (`gh label create "parity:approve" ...`) added to `parity init` completion output and README.
- `parity.yaml.example` restructured: single-platform primary example (LangSmith), other platforms as commented-out blocks. Added inline comments for hint patterns, guardrail artifacts, cost control, and platform config sections.
- `parity-spec-addendum.md` Gap 4: `parity resolve-run-id` command fully specified.

### Removed
- `.github/workflows/parity.yml` removed from the repository root. The reference workflow lives at `examples/langgraph-agentic-rag/.github/workflows/parity.yml`. The root copy was misconfigured (no `parity.yaml`, package not published) and fired on every PR, failing with exit code 1.

---

## 2026-03-14

### Added
- First-class Stage 2 bootstrap coverage mode for repositories with no usable eval corpus.
- PR comment messaging that distinguishes coverage-aware analysis from bootstrap starter-probe generation.
- Prompt and model tests covering empty-corpus handling.

### Changed
- Stage 2 coverage summaries now record `mode`, `corpus_status`, and `bootstrap_reason`.
- Stage 3 prompts now receive coverage summary context and explicit bootstrap-mode instructions.
- Docs and specs now state that Parity works without pre-existing evals, while improving with more eval coverage and richer product context.
