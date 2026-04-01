# Changelog

## 0.1.11

### Highlights

- Method-first pipeline: Stage 1 behavior analysis, Stage 2 eval analysis, Stage 3 native eval synthesis, deterministic writeback.
- Native writeback for LangSmith, Promptfoo, Braintrust, and Arize Phoenix where Parity can render safely.
- Host-owned Stage 2 and Stage 3 evidence tools with degraded fallback instead of hidden failure.
- Fixed GitHub approval contract: `parity:approve`.

### Notes

- Public docs now focus on the supported path.
- Deprecated workflow-policy config sections are kept only for backward-compatible parsing and are ignored by the supported scaffold/runtime path.
