# Changelog

## 2026-03-14

### Added
- First-class Stage 2 bootstrap coverage mode for repositories with no usable eval corpus.
- PR comment messaging that distinguishes coverage-aware analysis from bootstrap starter-probe generation.
- Prompt and model tests covering empty-corpus handling.

### Changed
- Stage 2 coverage summaries now record `mode`, `corpus_status`, and `bootstrap_reason`.
- Stage 3 prompts now receive coverage summary context and explicit bootstrap-mode instructions.
- Docs and specs now state that Probegen works without pre-existing evals, while improving with more eval coverage and richer product context.
