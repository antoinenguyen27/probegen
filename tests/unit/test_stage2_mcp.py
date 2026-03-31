from __future__ import annotations

import asyncio
from pathlib import Path

from parity.config import ParityConfig
from parity.stages.stage2_mcp import Stage2Toolbox, build_stage2_mcp_server


def test_stage2_toolbox_discovers_promptfoo_targets(tmp_path: Path) -> None:
    config_path = tmp_path / "evals" / "promptfooconfig.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "tests:\n"
        "  - description: sample\n"
        "    vars:\n"
        "      query: hi\n"
        "    assert:\n"
        "      - type: contains\n"
        "        value: hello\n",
        encoding="utf-8",
    )

    toolbox = Stage2Toolbox(
        config=ParityConfig.model_validate(
            {"platforms": {"promptfoo": {"config_path": "evals/promptfooconfig.yaml"}}}
        ),
        repo_root=tmp_path,
    )

    payload = toolbox.discover_eval_targets("promptfoo", "promptfoo", limit=5)

    assert payload["platform"] == "promptfoo"
    assert payload["candidates"][0]["target"] == "evals/promptfooconfig.yaml"


def test_stage2_toolbox_fetches_promptfoo_snapshot_from_repo_relative_path(tmp_path: Path) -> None:
    config_path = tmp_path / "evals" / "promptfooconfig.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "tests:\n"
        "  - id: case_001\n"
        "    vars:\n"
        "      query: hi\n"
        "    assert:\n"
        "      - type: contains\n"
        "        value: hello\n"
        "      - type: llm-rubric\n"
        "        value: The reply is friendly.\n",
        encoding="utf-8",
    )

    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)
    payload = toolbox.fetch_eval_target_snapshot("promptfoo", target="evals/promptfooconfig.yaml")

    assert payload["sample_count"] == 1
    assert payload["samples"][0]["case_id"] == "case_001"
    assert payload["method_profile"]["method_kind"] == "hybrid"
    assert payload["evaluator_dossiers"][0]["binding_id"] == "promptfoo::llm-rubric"
    recovery_state = toolbox.build_recovery_state()
    assert recovery_state["cached_target_snapshots"][0]["target_id"] == "promptfoo::evals/promptfooconfig.yaml"


def test_stage2_toolbox_rejects_promptfoo_paths_outside_repo(tmp_path: Path) -> None:
    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)

    try:
        toolbox.fetch_eval_target_snapshot("promptfoo", target="../secret.yaml")
    except FileNotFoundError as exc:
        assert "within the repository" in str(exc)
    else:
        raise AssertionError("Expected promptfoo fetch to reject paths outside the repo")


def test_stage2_server_exposes_expected_host_owned_tools(tmp_path: Path) -> None:
    bundle = build_stage2_mcp_server(config=ParityConfig(), repo_root=tmp_path)
    tool_names = sorted(tool.name for tool in asyncio.run(bundle.server.list_tools()))

    assert tool_names == [
        "discover_eval_targets",
        "discover_repo_eval_assets",
        "discover_target_evaluators",
        "embed_batch",
        "fetch_eval_target_snapshot",
        "find_similar",
        "find_similar_batch",
        "list_platform_evaluator_capabilities",
        "read_evaluator_binding",
        "read_repo_eval_asset",
        "verify_evaluator_binding",
    ]


def test_stage2_server_exposes_low_level_mcp_instance_for_sdk_transport(tmp_path: Path) -> None:
    bundle = build_stage2_mcp_server(config=ParityConfig(), repo_root=tmp_path)

    assert hasattr(bundle.server, "_mcp_server")
    assert hasattr(bundle.server._mcp_server, "request_handlers")


def test_stage2_toolbox_blocks_embedding_request_when_spend_cap_would_be_exceeded(tmp_path: Path) -> None:
    toolbox = Stage2Toolbox(
        config=ParityConfig(),
        repo_root=tmp_path,
        embedding_spend_cap_usd=0.0,
    )

    payload = toolbox.embed_batch([{"id": "case_1", "text": "Hello from a new eval case"}])

    assert payload["budget_exceeded"] is True
    assert payload["count"] == 0
    assert payload["missing_ids"] == ["case_1"]
    assert toolbox.build_runtime_metadata()["embedding"]["blocked_request_count"] == 1


def test_stage2_toolbox_discovers_repo_eval_code_assets(tmp_path: Path) -> None:
    code_path = tmp_path / "evals" / "answer_scorer.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("def score_answer(output):\n    return 1.0\n", encoding="utf-8")

    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)
    payload = toolbox.discover_repo_eval_assets(query="scorer", limit=10)

    assert payload["count"] == 1
    assert payload["candidates"][0]["kind"] == "repo_eval_code_asset"


def test_stage2_toolbox_lists_platform_evaluator_capabilities(tmp_path: Path) -> None:
    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)

    payload = toolbox.list_platform_evaluator_capabilities("langsmith")

    assert payload["platform"] == "langsmith"
    assert payload["supports_formal_discovery"] is True
    assert payload["supports_evaluator_reuse"] is True
    assert payload["supports_binding_verification"] is True


def test_stage2_toolbox_discovers_promptfoo_formal_evaluators(tmp_path: Path) -> None:
    config_path = tmp_path / "evals" / "promptfooconfig.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "tests:\n"
        "  - id: case_001\n"
        "    vars:\n"
        "      query: hi\n"
        "    assert:\n"
        "      - type: llm-rubric\n"
        "        value: Friendly reply.\n",
        encoding="utf-8",
    )

    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)
    payload = toolbox.discover_target_evaluators("promptfoo", target="evals/promptfooconfig.yaml")

    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["discovery_mode"] == "formal"
    assert payload["candidates"][0]["verification_status"] == "verified"


def test_stage2_toolbox_discovers_braintrust_repo_formal_evaluators(tmp_path: Path) -> None:
    scorer_path = tmp_path / "evals" / "support_answer_scorer.py"
    scorer_path.parent.mkdir(parents=True, exist_ok=True)
    scorer_path.write_text("def score_answer(output, expected):\n    return 1.0\n", encoding="utf-8")

    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)
    payload = toolbox.discover_target_evaluators(
        "braintrust",
        target="support-answer-dataset",
        project="support",
    )

    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["discovery_mode"] == "repo_formal"
    assert payload["candidates"][0]["binding_location"] == "evals/support_answer_scorer.py"
