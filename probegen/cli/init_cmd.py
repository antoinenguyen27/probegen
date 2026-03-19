from __future__ import annotations

import ast
import fnmatch
import os
from pathlib import Path
from typing import Iterable

import click
import yaml

from probegen.config import (
    ApprovalConfig,
    ArtifactDetectionConfig,
    AutoRunConfig,
    BudgetsConfig,
    ContextConfig,
    EmbeddingConfig,
    GenerationConfig,
    LangSmithPlatformConfig,
    BraintrustPlatformConfig,
    ArizePhoenixPlatformConfig,
    PlatformsConfig,
    PromptfooPlatformConfig,
    ProbegenConfig,
    SimilarityConfig,
    MappingConfig,
)

IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
PROMPT_KEYWORDS = (
    "you are",
    "your role is",
    "your task is",
    "always",
    "never",
    "when the user",
    "respond with",
)
BEHAVIOR_NAME_PATTERNS = (
    "*prompt*.txt",
    "*prompt*.md",
    "*prompt*.yaml",
    "*prompt*.json",
    "*prompt*.j2",
    "*instruction*.txt",
    "*instruction*.md",
    "*instruction*.yaml",
    "*system*.txt",
    "*system*.md",
    "*system*.yaml",
)
GUARDRAIL_PATH_PARTS = {"judge", "validator", "guardrail", "classifier", "filter", "rubric", "safety"}
PYTHON_BEHAVIOR_PATTERNS = ("*_prompt", "*_instruction", "system_*", "*_template")
PYTHON_GUARDRAIL_PATTERNS = ("*_judge*", "*_validator*", "*_classifier*", "*_filter*", "*_rubric*")

PRODUCT_STUB = """# Product Context

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
"""

USERS_STUB = """# User Profiles

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
"""

INTERACTIONS_STUB = """# Interaction Patterns

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
"""

GOOD_EXAMPLES_STUB = """# What Good Looks Like

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
"""

BAD_EXAMPLES_STUB = """# Known Failure Modes

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
"""

TRACES_README = """# Production Traces

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
"""

WORKFLOW_TEMPLATE = """name: Probegen

on:
  pull_request:
    types: [opened, synchronize, reopened]
  pull_request_target:
    types: [closed]

permissions:
  actions: read
  contents: read
  pull-requests: write

jobs:
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
          probegen run-stage 1 \\
            --pr-number ${{ github.event.pull_request.number }} \\
            --base-branch ${{ github.event.pull_request.base.ref }} \\
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
          probegen run-stage 2 \\
            --manifest .probegen/stage1.json \\
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
          probegen run-stage 3 \\
            --manifest .probegen/stage1.json \\
            --gaps .probegen/stage2.json \\
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
          probegen post-comment \\
            --proposal .probegen/stage3.json \\
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
          run_id=$(probegen resolve-run-id \\
            --repo ${{ github.repository }} \\
            --workflow-id probegen.yml \\
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
          probegen write-probes \\
            --proposal .probegen/stage3.json \\
            --config probegen.yaml
        env:
          LANGSMITH_API_KEY: ${{ secrets.LANGSMITH_API_KEY }}
          BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
          PHOENIX_API_KEY: ${{ secrets.PHOENIX_API_KEY }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          COMMIT_SHA: ${{ github.event.pull_request.merge_commit_sha }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_RUN_ID: ${{ github.run_id }}
"""


def _iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORE_DIRS]
        for filename in filenames:
            yield Path(dirpath) / filename


def _python_symbols(path: Path) -> tuple[set[str], set[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return set(), set()
    assignments: set[str] = set()
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assignments.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    return assignments, symbols


def scan_behavior_artifacts(root: Path) -> list[str]:
    detected: set[str] = set()
    for path in _iter_files(root):
        rel = str(path.relative_to(root))
        lowered = rel.lower()
        if any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in BEHAVIOR_NAME_PATTERNS):
            detected.add(rel)
            continue
        if path.suffix.lower() in {".md", ".txt", ".yaml", ".yml", ".json"}:
            try:
                head = path.read_text(encoding="utf-8")[:500].lower()
            except Exception:
                head = ""
            if any(keyword in head for keyword in PROMPT_KEYWORDS):
                detected.add(rel)
                continue
        if path.suffix == ".py":
            assignments, _ = _python_symbols(path)
            if any(
                any(fnmatch.fnmatch(name, pattern) for pattern in PYTHON_BEHAVIOR_PATTERNS)
                for name in assignments
            ):
                detected.add(rel)
    return sorted(detected)


def scan_guardrail_artifacts(root: Path) -> list[str]:
    detected: set[str] = set()
    for path in _iter_files(root):
        rel = str(path.relative_to(root))
        parts = {part.lower() for part in path.parts}
        if parts & GUARDRAIL_PATH_PARTS:
            detected.add(rel)
            continue
        if path.suffix == ".py":
            _, symbols = _python_symbols(path)
            if any(
                any(fnmatch.fnmatch(name.lower(), pattern) for pattern in PYTHON_GUARDRAIL_PATTERNS)
                for name in symbols
            ):
                detected.add(rel)
    return sorted(detected)


def _confirm_list(prompt: str, items: list[str]) -> list[str]:
    if not items:
        return []
    click.echo(prompt)
    for item in items:
        click.echo(f"  - {item}")
    response = click.prompt("", default="Y")
    lowered = response.strip().lower()
    if lowered in {"y", "yes", ""}:
        return items
    if lowered in {"n", "no"}:
        return []
    if lowered == "edit":
        edited = click.prompt("Enter newline or comma separated paths", default="\n".join(items))
        normalized = [entry.strip() for chunk in edited.splitlines() for entry in chunk.split(",") if entry.strip()]
        return normalized
    return items


def _selected_platforms() -> PlatformsConfig:
    click.echo("3. Which eval platform do you use?")
    click.echo("  [1] LangSmith  [2] Braintrust  [3] Arize Phoenix  [4] Promptfoo  [5] None / file export")
    choice = click.prompt("Selection", default="5").strip()
    platforms = PlatformsConfig()
    for item in {part.strip() for part in choice.split(",") if part.strip()}:
        if item == "1":
            platforms.langsmith = LangSmithPlatformConfig()
        elif item == "2":
            platforms.braintrust = BraintrustPlatformConfig()
        elif item == "3":
            platforms.arize_phoenix = ArizePhoenixPlatformConfig()
        elif item == "4":
            platforms.promptfoo = PromptfooPlatformConfig()
    return platforms


def _default_mapping_platform(platforms: PlatformsConfig) -> str | None:
    for name in ("langsmith", "braintrust", "arize_phoenix", "promptfoo"):
        if getattr(platforms, name):
            return name
    return None


def _create_context_stubs(root: Path, *, dry_run: bool) -> None:
    files = {
        root / "context" / "product.md": PRODUCT_STUB,
        root / "context" / "users.md": USERS_STUB,
        root / "context" / "interactions.md": INTERACTIONS_STUB,
        root / "context" / "good_examples.md": GOOD_EXAMPLES_STUB,
        root / "context" / "bad_examples.md": BAD_EXAMPLES_STUB,
        root / "context" / "traces" / "README.md": TRACES_README,
    }
    if dry_run:
        for path in files:
            click.echo(f"Would create {path.relative_to(root)}")
        return
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(content, encoding="utf-8")


@click.command("init")
@click.option("--context-only", is_flag=True, help="Create or refresh only the context pack stubs.")
@click.option("--dry-run", is_flag=True, help="Print what would be created without writing files.")
def init_command(context_only: bool, dry_run: bool) -> None:
    root = Path.cwd()
    try:
        if context_only:
            _create_context_stubs(root, dry_run=dry_run)
            return

        behavior = _confirm_list(
            "1. Detected these likely behavior-defining artifacts (hint patterns to help Probegen focus faster):",
            scan_behavior_artifacts(root),
        )
        guardrails = _confirm_list(
            "2. Detected these likely guardrail artifacts (hint patterns for judges, validators, classifiers):",
            scan_guardrail_artifacts(root),
        )
        platforms = _selected_platforms()
        mapping_platform = _default_mapping_platform(platforms)

        mappings: list[MappingConfig] = []
        if mapping_platform:
            for artifact in behavior + guardrails:
                dataset = click.prompt(
                    f"4. For artifact '{artifact}', which dataset contains existing evals for this artifact? (blank to start in bootstrap mode)",
                    default="",
                    show_default=False,
                ).strip()
                if dataset:
                    mappings.append(
                        MappingConfig(
                            artifact=artifact,
                            platform=mapping_platform,  # type: ignore[arg-type]
                            dataset=dataset,
                        )
                    )

        create_context = click.confirm(
            "5. Create a context/ directory with stub files?",
            default=True,
        )

        config = ProbegenConfig(
            behavior_artifacts=ArtifactDetectionConfig(paths=behavior, python_patterns=list(PYTHON_BEHAVIOR_PATTERNS), exclude=["tests/**", "*.test.yaml", "docs/**"]),
            guardrail_artifacts=ArtifactDetectionConfig(paths=guardrails, python_patterns=list(PYTHON_GUARDRAIL_PATTERNS)),
            context=ContextConfig(),
            platforms=platforms,
            mappings=mappings,
            embedding=EmbeddingConfig(),
            similarity=SimilarityConfig(),
            generation=GenerationConfig(),
            approval=ApprovalConfig(),
            auto_run=AutoRunConfig(),
            budgets=BudgetsConfig(),
        )

        config_yaml = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        if dry_run:
            click.echo("Would write probegen.yaml")
            click.echo(config_yaml)
            click.echo("Would write .github/workflows/probegen.yml")
        else:
            (root / "probegen.yaml").write_text(config_yaml, encoding="utf-8")
            workflow_path = root / ".github" / "workflows" / "probegen.yml"
            workflow_path.parent.mkdir(parents=True, exist_ok=True)
            workflow_path.write_text(WORKFLOW_TEMPLATE, encoding="utf-8")

        if create_context:
            _create_context_stubs(root, dry_run=dry_run)

        click.echo("")
        click.echo("Setup complete. Next steps:")
        click.echo("  1. Fill in context/ files with product details and known failure modes.")
        click.echo("  2. Add GitHub secrets: ANTHROPIC_API_KEY, OPENAI_API_KEY (+ eval platform keys).")
        click.echo("  3. Create the approval label in GitHub:")
        click.echo('       gh label create "probegen:approve" --color 0075ca --description "Approve Probegen probe writeback"')
        click.echo("  4. Commit probegen.yaml, .github/workflows/probegen.yml, and context/.")
        click.echo("  5. Run `probegen doctor` to verify your setup.")
    except click.Abort as exc:
        raise SystemExit(1) from exc
    except OSError as exc:
        click.echo(f"probegen init: write error: {exc}", err=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    init_command()
