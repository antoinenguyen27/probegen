from __future__ import annotations

import random
from pathlib import Path

from parity.config import ParityConfig
from parity.context import (
    ContextPack,
    count_tokens,
    load_context_pack,
    sample_traces,
    trim_collection_to_budget,
    truncate_text,
)


def test_truncate_text_noop_when_within_budget() -> None:
    assert truncate_text("hello", 10) == "hello"


def test_truncate_text_applies_marker_when_needed() -> None:
    text = "one two three four five six seven eight nine ten"
    truncated = truncate_text(text, 5)
    assert truncated.endswith("[truncated]")
    assert count_tokens(truncated) <= 5


def test_load_context_pack_warns_but_returns_empty_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "parity.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = ParityConfig.load(config_path)

    pack = load_context_pack(config, repo_root=tmp_path, emit_warnings=False)

    assert isinstance(pack, ContextPack)
    assert pack.product == ""
    assert pack.warnings


def test_sample_traces_reads_txt_and_json(tmp_path: Path) -> None:
    traces_dir = tmp_path / "context" / "traces"
    traces_dir.mkdir(parents=True)
    (traces_dir / "a.txt").write_text("USER: hi", encoding="utf-8")
    (traces_dir / "b.json").write_text(
        '[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]',
        encoding="utf-8",
    )

    traces = sample_traces(traces_dir, max_samples=2, rng=random.Random(1))

    assert len(traces) == 2
    assert any("USER: hello" in trace for trace in traces)


def test_sample_traces_ignores_non_txt_json_files(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "keep.txt").write_text("trace content", encoding="utf-8")
    (traces_dir / "skip.md").write_text("markdown ignored", encoding="utf-8")
    (traces_dir / "skip.py").write_text("python ignored", encoding="utf-8")

    traces = sample_traces(traces_dir, max_samples=10)

    assert len(traces) == 1
    assert traces[0] == "trace content"


def test_sample_traces_returns_empty_for_missing_directory(tmp_path: Path) -> None:
    result = sample_traces(tmp_path / "nonexistent", max_samples=5)
    assert result == []


def test_sample_traces_returns_empty_when_max_samples_zero(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "a.txt").write_text("content", encoding="utf-8")
    assert sample_traces(traces_dir, max_samples=0) == []


class TestTrimCollectionToBudget:
    def test_returns_all_items_within_budget(self) -> None:
        texts = ["hello", "world"]
        result = trim_collection_to_budget(texts, per_item_budget=100, total_budget=500)
        assert result == ["hello", "world"]

    def test_truncates_individual_items_exceeding_per_item_budget(self) -> None:
        # A long text should be cut to per_item_budget tokens
        long_text = " ".join(["word"] * 200)
        result = trim_collection_to_budget([long_text], per_item_budget=10, total_budget=1000)
        assert len(result) == 1
        assert count_tokens(result[0]) <= 10

    def test_stops_adding_items_when_total_budget_exhausted(self) -> None:
        # Each item is ~5 tokens; total_budget of 8 should fit only ~1 item
        texts = ["hello world foo bar baz"] * 10
        result = trim_collection_to_budget(texts, per_item_budget=100, total_budget=6)
        assert len(result) < len(texts)

    def test_returns_empty_list_when_total_budget_is_zero(self) -> None:
        result = trim_collection_to_budget(["hello"], per_item_budget=100, total_budget=0)
        assert result == []

    def test_handles_empty_input(self) -> None:
        result = trim_collection_to_budget([], per_item_budget=100, total_budget=500)
        assert result == []
