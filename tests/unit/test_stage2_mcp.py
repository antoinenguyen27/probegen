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

    payload = toolbox.search_eval_targets("promptfoo", "promptfoo", limit=5)

    assert payload["platform"] == "promptfoo"
    assert payload["candidates"][0]["target"] == "evals/promptfooconfig.yaml"


def test_stage2_toolbox_fetches_promptfoo_cases_from_repo_relative_path(tmp_path: Path) -> None:
    config_path = tmp_path / "evals" / "promptfooconfig.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "tests:\n"
        "  - id: case_001\n"
        "    vars:\n"
        "      query: hi\n"
        "    assert:\n"
        "      - type: contains\n"
        "        value: hello\n",
        encoding="utf-8",
    )

    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)
    payload = toolbox.fetch_eval_cases("promptfoo", target="evals/promptfooconfig.yaml")

    assert payload["case_count"] == 1
    assert payload["cases"][0]["id"] == "case_001"


def test_stage2_toolbox_rejects_promptfoo_paths_outside_repo(tmp_path: Path) -> None:
    toolbox = Stage2Toolbox(config=ParityConfig(), repo_root=tmp_path)

    try:
        toolbox.fetch_eval_cases("promptfoo", target="../secret.yaml")
    except FileNotFoundError as exc:
        assert "within the repository" in str(exc)
    else:
        raise AssertionError("Expected promptfoo fetch to reject paths outside the repo")


def test_stage2_server_exposes_expected_host_owned_tools(tmp_path: Path) -> None:
    bundle = build_stage2_mcp_server(config=ParityConfig(), repo_root=tmp_path)
    tool_names = sorted(tool.name for tool in asyncio.run(bundle.server.list_tools()))

    assert tool_names == [
        "embed_batch",
        "fetch_eval_cases",
        "find_similar",
        "find_similar_batch",
        "search_eval_targets",
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
