from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import tiktoken

from parity.config import ParityConfig

TRUNCATED_MARKER = "\n[truncated]"
_ENCODING_CACHE: object | None = None
_FALLBACK_WARNING_EMITTED = False


class _SimpleEncoding:
    """Offline fallback when tiktoken resources are unavailable."""

    token_pattern = re.compile(r"\S+\s*")

    def encode(self, text: str) -> list[str]:
        if not text:
            return []
        return self.token_pattern.findall(text)

    def decode(self, tokens: list[str]) -> str:
        return "".join(tokens)


def get_encoding() -> tiktoken.Encoding:
    global _ENCODING_CACHE, _FALLBACK_WARNING_EMITTED
    if _ENCODING_CACHE is not None:
        return _ENCODING_CACHE  # type: ignore[return-value]

    try:
        _ENCODING_CACHE = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODING_CACHE = _SimpleEncoding()
        if not _FALLBACK_WARNING_EMITTED:
            _warn(
                "Falling back to approximate token counting because cl100k_base could not be loaded."
            )
            _FALLBACK_WARNING_EMITTED = True
    return _ENCODING_CACHE  # type: ignore[return-value]


def count_tokens(text: str) -> int:
    return len(get_encoding().encode(text))


def truncate_text(text: str, max_tokens: int, marker: str = TRUNCATED_MARKER) -> str:
    if max_tokens <= 0 or not text:
        return ""

    encoding = get_encoding()
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text

    marker_tokens = encoding.encode(marker)
    if len(marker_tokens) > max_tokens:
        return encoding.decode(tokens[:max_tokens])

    budget = max_tokens - len(marker_tokens)
    return f"{encoding.decode(tokens[:budget]).rstrip()}{marker}"


def _warn(message: str) -> None:
    print(f"parity: warning: {message}", file=sys.stderr)


@dataclass(slots=True)
class ContextPack:
    product: str = ""
    users: str = ""
    interactions: str = ""
    good_examples: str = ""
    bad_examples: str = ""
    traces_dir: Path | None = None
    trace_max_samples: int = 20
    warnings: list[str] = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return not any(
            [
                self.product,
                self.users,
                self.interactions,
                self.good_examples,
                self.bad_examples,
            ]
        )


def _read_optional_file(path: Path, warnings: list[str]) -> str:
    if not path.exists():
        warnings.append(f"Missing context file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def load_context_pack(
    config: ParityConfig,
    repo_root: str | Path | None = None,
    *,
    emit_warnings: bool = True,
) -> ContextPack:
    warnings: list[str] = []
    base = Path(repo_root) if repo_root is not None else Path.cwd()
    context = config.context

    pack = ContextPack(
        product=_read_optional_file(config.resolve_path(context.product, base), warnings),
        users=_read_optional_file(config.resolve_path(context.users, base), warnings),
        interactions=_read_optional_file(config.resolve_path(context.interactions, base), warnings),
        good_examples=_read_optional_file(config.resolve_path(context.good_examples, base), warnings),
        bad_examples=_read_optional_file(config.resolve_path(context.bad_examples, base), warnings),
        traces_dir=config.resolve_path(context.traces_dir, base),
        trace_max_samples=context.trace_max_samples,
        warnings=warnings,
    )

    if emit_warnings and warnings:
        for warning in warnings:
            _warn(warning)
        _warn(
            "Context pack not configured completely; probe quality may be reduced."
        )

    return pack


def _load_trace(path: Path) -> str:
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return path.read_text(encoding="utf-8")
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            lines = []
            for item in data:
                role = str(item.get("role", "unknown")).upper()
                content = str(item.get("content", ""))
                lines.append(f"{role}: {content}")
            return "\n".join(lines)
        return json.dumps(data, ensure_ascii=True, indent=2)
    return path.read_text(encoding="utf-8")


def sample_traces(
    traces_dir: str | Path | None,
    *,
    max_samples: int,
    rng: random.Random | None = None,
) -> list[str]:
    if traces_dir is None or max_samples <= 0:
        return []

    directory = Path(traces_dir)
    if not directory.exists() or not directory.is_dir():
        return []

    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".txt", ".json"}
    )
    if not paths:
        return []

    selector = rng or random
    chosen = paths if len(paths) <= max_samples else selector.sample(paths, max_samples)
    return [_load_trace(path) for path in chosen]


def trim_collection_to_budget(
    texts: Iterable[str],
    *,
    per_item_budget: int,
    total_budget: int,
) -> list[str]:
    trimmed: list[str] = []
    remaining = total_budget
    for text in texts:
        if remaining <= 0:
            break
        item_budget = min(per_item_budget, remaining)
        truncated = truncate_text(text, item_budget)
        trimmed.append(truncated)
        remaining -= count_tokens(truncated)
    return trimmed
