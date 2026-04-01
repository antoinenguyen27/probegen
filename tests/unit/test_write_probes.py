from __future__ import annotations

import json
from pathlib import Path

from parity.cli.write_evals import write_evals_from_proposal
from parity.config import ParityConfig, PlatformsConfig, PromptfooPlatformConfig
from parity.models import EvalProposalManifest

_FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_write_evals_uses_target_locator_and_counts_success(tmp_path: Path) -> None:
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))
    proposal.targets[0].locator = str(tmp_path / "promptfooconfig.yaml")
    config = ParityConfig(
        platforms=PlatformsConfig(promptfoo=PromptfooPlatformConfig(config_path=str(tmp_path / "promptfooconfig.yaml")))
    )

    outcome = write_evals_from_proposal(
        proposal,
        config=config,
        repo_root=tmp_path,
    )

    assert outcome.exit_code == 0
    assert outcome.total_written == 2
    assert outcome.written_targets == [f"promptfoo:{proposal.targets[0].target_name}"]
    assert (tmp_path / "promptfooconfig.yaml").exists()


def test_write_evals_skips_review_only_and_unsupported_renderings(tmp_path: Path) -> None:
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))
    proposal.targets[0].locator = str(tmp_path / "promptfooconfig.yaml")
    proposal.renderings[0].write_status = "review_only"
    proposal.renderings[1].write_status = "unsupported"
    proposal.renderings[1].abstention_reason = "Unsupported method."

    outcome = write_evals_from_proposal(
        proposal,
        config=ParityConfig(
            platforms=PlatformsConfig(promptfoo=PromptfooPlatformConfig(config_path=str(tmp_path / "promptfooconfig.yaml")))
        ),
        repo_root=tmp_path,
    )

    assert outcome.exit_code == 0
    assert outcome.total_written == 0
    assert outcome.skipped_review_only == [f"promptfoo:{proposal.targets[0].target_name}"]
    assert outcome.unsupported_targets == [f"promptfoo:{proposal.targets[0].target_name}"]


def test_write_evals_rejects_promptfoo_targets_outside_repo_root(tmp_path: Path) -> None:
    proposal = EvalProposalManifest.model_validate(_load_fixture("sample_proposal.json"))
    outside_target = tmp_path.parent / "outside-promptfoo.yaml"
    proposal.targets[0].locator = str(outside_target)

    outcome = write_evals_from_proposal(
        proposal,
        config=ParityConfig(
            platforms=PlatformsConfig(promptfoo=PromptfooPlatformConfig(config_path=str(outside_target)))
        ),
        repo_root=tmp_path,
    )

    assert outcome.exit_code == 2
    assert outcome.total_written == 0
    assert outcome.failures == [
        f"promptfoo:{proposal.targets[0].target_name}: Promptfoo write target must stay within the repository root: {outside_target}"
    ]


def test_write_evals_rejects_braintrust_targets_without_project() -> None:
    proposal = EvalProposalManifest.model_validate(
        {
            "run_id": "stage3",
            "stage1_run_id": "stage1",
            "stage2_run_id": "stage2",
            "stage3_run_id": "stage3",
            "timestamp": "2026-04-01T00:00:00Z",
            "pr_number": 1,
            "commit_sha": "abc123",
            "intent_count": 1,
            "targets": [
                {
                    "target_id": "braintrust::demo",
                    "platform": "braintrust",
                    "locator": "demo-dataset",
                    "target_name": "demo-dataset",
                    "dataset_id": None,
                    "project": None,
                    "artifact_paths": ["app/graph.py"],
                    "resolution_source": "platform_discovery",
                    "access_mode": "sdk",
                    "write_capability": "native_ready",
                    "profile_confidence": 0.9,
                }
            ],
            "intents": [
                {
                    "intent_id": "intent_001",
                    "gap_id": "gap_001",
                    "target_id": "braintrust::demo",
                    "method_kind": "deterministic",
                    "intent_type": "regression_guard",
                    "title": "Demo",
                    "is_conversational": False,
                    "input": "What year was the Paris Agreement signed?",
                    "input_format": "string",
                    "behavior_under_test": "The assistant returns the correct year.",
                    "pass_criteria": "2015",
                    "failure_mode": "Wrong year.",
                    "probe_rationale": "Guard factual regressions.",
                    "related_risk_flag": "Wrong factual answer.",
                    "specificity_confidence": 0.9,
                    "testability_confidence": 0.9,
                    "novelty_confidence": 0.8,
                    "realism_confidence": 0.8,
                    "target_fit_confidence": 0.9,
                }
            ],
            "evaluator_plans": [],
            "renderings": [
                {
                    "rendering_id": "render-intent_001",
                    "intent_id": "intent_001",
                    "target_id": "braintrust::demo",
                    "method_kind": "deterministic",
                    "rendering_kind": "braintrust_record",
                    "renderer_id": "braintrust/dataset-record",
                    "write_status": "native_ready",
                    "render_confidence": 0.95,
                    "target_locator": "demo-dataset",
                    "payload": {
                        "input": "What year was the Paris Agreement signed?",
                        "expected": "2015",
                        "metadata": {},
                        "tags": [],
                    },
                    "summary": "Braintrust-native dataset record ready for deterministic writeback.",
                }
            ],
            "render_artifacts": [],
            "warnings": [],
        }
    )

    outcome = write_evals_from_proposal(proposal, config=ParityConfig())

    assert outcome.exit_code == 2
    assert outcome.failures == [
        "braintrust:demo-dataset: Braintrust write target is missing required `project` metadata."
    ]
