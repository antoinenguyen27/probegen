# Changelog

## 2026-03-16

### Changed
- README now accurately describes the full artifact scope Probegen detects: prompts, instructions, guardrails, validators, tool descriptions, classifiers, retry policies, output schemas, and other agent harness artifacts. Previously undersold as "prompt, instruction, and guardrail changes" only.
- README platform reference corrected from "Phoenix" to "Arize Phoenix" for consistency with spec and config.
- README probe description updated to mention multi-turn conversational probe generation.
- README Setup section: all absolute `/Users/an/...` links replaced with correct relative paths. Workflow reference updated to point to `examples/langgraph-agentic-rag/.github/workflows/probegen.yml` now that the root workflow has been removed.
- README quickstart link corrected to `examples/langgraph-agentic-rag/docs/quickstart.md` (was pointing to a non-existent `docs/langgraph-agentic-rag-quickstart.md`).
- `probegen-spec-addendum.md` Gap 2: Stage 3 token budget fallback behaviour corrected. Spec previously stated traces are reduced first, then examples. Implementation reduces `good_examples` (3,000→1,500 tokens) and `bad_examples` (4,000→2,000 tokens) first, then drops traces entirely. Addendum now documents actual behaviour.
- `probegen-spec-addendum.md` Gap 2: Stage 2 stripping comment updated to explicitly name all three stripped fields (`raw_diff`, `before_content`, `after_content`), not just `raw_diff`.
- `probegen-spec.md` Step 5 of the setup checklist reworded to describe artifact matching by `probegen.yaml` path patterns rather than listing specific file types.

### Added
- `probegen-spec-addendum.md` Gap 4: `probegen resolve-run-id` command fully specified. This command existed in the implementation and was used by the Stage 4 workflow job but had no spec entry.

### Removed
- `.github/workflows/probegen.yml` removed from the repository root. Probegen's reference workflow already lives at `examples/langgraph-agentic-rag/.github/workflows/probegen.yml` as part of the self-contained demo. The root copy was misconfigured (no `probegen.yaml`, package not published) and fired on every PR, failing with exit code 1.

---

## 2026-03-14

### Added
- First-class Stage 2 bootstrap coverage mode for repositories with no usable eval corpus.
- PR comment messaging that distinguishes coverage-aware analysis from bootstrap starter-probe generation.
- Prompt and model tests covering empty-corpus handling.

### Changed
- Stage 2 coverage summaries now record `mode`, `corpus_status`, and `bootstrap_reason`.
- Stage 3 prompts now receive coverage summary context and explicit bootstrap-mode instructions.
- Docs and specs now state that Probegen works without pre-existing evals, while improving with more eval coverage and richer product context.
