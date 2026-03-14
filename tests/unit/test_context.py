from __future__ import annotations

import random
from pathlib import Path

from probegen.config import ProbegenConfig
from probegen.context import ContextPack, count_tokens, load_context_pack, sample_traces, truncate_text


def test_truncate_text_noop_when_within_budget() -> None:
    assert truncate_text("hello", 10) == "hello"


def test_truncate_text_applies_marker_when_needed() -> None:
    text = "one two three four five six seven eight nine ten"
    truncated = truncate_text(text, 5)
    assert truncated.endswith("[truncated]")
    assert count_tokens(truncated) <= 5


def test_load_context_pack_warns_but_returns_empty_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "probegen.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    config = ProbegenConfig.load(config_path)

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
