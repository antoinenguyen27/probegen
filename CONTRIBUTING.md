# Contributing

## Scope

Parity is a developer tool. Good contributions usually improve one of these areas:

- behavior change detection
- eval target discovery
- native rendering and writeback
- safety and determinism
- docs and maintainer ergonomics

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Before Opening a PR

Run:

```bash
pytest -m "not integration"
pytest -m integration -v
python -m build
```

If you change packaging or release behavior, also verify the package build path from the built wheel.

## Docs Expectations

If you change the public behavior of Parity:

- update the root README if the supported path changes
- update `docs/configuration.md` if real config behavior changes
- update `docs/platforms.md` if platform support strength changes
- keep the generated workflow and example workflow aligned

## Public Surface Discipline

Please avoid adding config or CLI surface area unless it provides a clear, testable DX improvement. Narrow, predictable behavior is preferred over partially-supported flexibility.

## Pull Requests

PRs are easier to review when they include:

- the user-facing problem being solved
- the intended contract after the change
- any docs or workflow updates required alongside the code
