# Maintainer Guide

This repository is the source for Parity itself. It is not a Parity-enabled consumer repo, so commands like `parity doctor` should usually be exercised in the example repo or in a temporary test repo.

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test Commands

```bash
pytest -m "not integration"
pytest -m integration -v
python -m build
```

## Package Verification

The CI workflow should be the source of truth for package verification:

1. build sdist and wheel
2. install from the built wheel in a fresh venv
3. run `parity --help`

## Example Repo

The end-to-end example lives in [`examples/langgraph-agentic-rag`](../examples/langgraph-agentic-rag/README.md).

Use it when you need to verify:

- `parity init` output shape
- the generated GitHub Actions workflow
- example `parity.yaml` behavior
- PR-to-writeback flow

The example workflow is expected to match `render_workflow_template(...)` exactly. `tests/unit/test_example_workflow.py` enforces that contract.

## Public Surface Discipline

When changing Parity, prefer these rules:

- Do not expose a config knob unless it changes user-visible behavior predictably.
- Keep the generated workflow and the example workflow aligned.
- Keep root docs focused on the supported path; move drift-prone history out of the critical path.
