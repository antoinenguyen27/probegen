from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai import OpenAI

from parity.context import count_tokens
from parity.errors import CacheError, EmbeddingError

EMBEDDING_MODEL_PRICES_USD_PER_1M_INPUT_TOKENS = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
}


def compute_text_hash(item_id: str, text: str) -> str:
    digest = sha256(f"{item_id}{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_cache_key(item_id: str, text: str, model: str, dimensions: int | None = None) -> str:
    suffix = "" if dimensions is None else str(dimensions)
    digest = sha256(f"{item_id}{text}{model}{suffix}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


@dataclass(slots=True)
class EmbeddingItem:
    id: str
    text: str


@dataclass(slots=True)
class EmbeddingBatchUsage:
    model: str
    request_count: int
    input_count: int
    cached_count: int
    miss_count: int
    input_tokens: int
    estimated_cost_usd: float | None

    def model_dump(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "request_count": self.request_count,
            "input_count": self.input_count,
            "cached_count": self.cached_count,
            "miss_count": self.miss_count,
            "input_tokens": self.input_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass(slots=True)
class PlannedEmbeddingBatch:
    items: list[EmbeddingItem]
    cached_results: dict[str, dict[str, Any]]
    misses: list[EmbeddingItem]
    cache_warning: bool
    usage: EmbeddingBatchUsage


class EmbeddingCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                cache_key TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER,
                embedding_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        return connection

    def get(
        self,
        *,
        item_id: str,
        text_hash: str,
        model: str,
        dimensions: int | None = None,
    ) -> list[float] | None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            row = connection.execute(
                """
                SELECT embedding_json
                FROM embeddings
                WHERE item_id = ? AND text_hash = ? AND model = ? AND dimensions IS ?
                """,
                (item_id, text_hash, model, dimensions),
            ).fetchone()
        except sqlite3.Error as exc:
            raise CacheError(f"Embedding cache read failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

        if row is None:
            return None
        return json.loads(row[0])

    def set(
        self,
        *,
        item_id: str,
        text_hash: str,
        model: str,
        embedding: list[float],
        dimensions: int | None = None,
    ) -> None:
        cache_key = compute_cache_key(item_id, text_hash, model, dimensions)
        created_at = datetime.now(tz=timezone.utc).isoformat()
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute(
                """
                INSERT OR REPLACE INTO embeddings (
                    cache_key,
                    item_id,
                    text_hash,
                    model,
                    dimensions,
                    embedding_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    item_id,
                    text_hash,
                    model,
                    dimensions,
                    json.dumps(embedding),
                    created_at,
                )
            )
            connection.commit()
        except sqlite3.Error as exc:
            raise CacheError(f"Embedding cache write failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()


def _request_embeddings(
    items: list[EmbeddingItem],
    *,
    model: str,
    dimensions: int | None = None,
    client: Any | None = None,
) -> tuple[list[list[float]], int]:
    openai_client = client or OpenAI()
    kwargs: dict[str, Any] = {"model": model, "input": [item.text for item in items]}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions

    try:
        response = openai_client.embeddings.create(**kwargs)
    except Exception as exc:
        raise EmbeddingError(f"Embedding API error: {exc}") from exc

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "total_tokens", None)
    if input_tokens is None:
        input_tokens = sum(count_tokens(item.text) for item in items)

    return [list(record.embedding) for record in response.data], int(input_tokens)


def resolve_embedding_input_price_usd_per_million(model: str) -> float | None:
    return EMBEDDING_MODEL_PRICES_USD_PER_1M_INPUT_TOKENS.get(model)


def estimate_embedding_cost_usd(*, model: str, input_tokens: int) -> float | None:
    price = resolve_embedding_input_price_usd_per_million(model)
    if price is None:
        return None
    return (input_tokens / 1_000_000) * price


def _normalize_embedding_inputs(
    inputs: list[dict[str, str]] | list[EmbeddingItem],
) -> list[EmbeddingItem]:
    return [
        item if isinstance(item, EmbeddingItem) else EmbeddingItem(id=item["id"], text=item["text"])
        for item in inputs
    ]


def plan_embedding_batch(
    inputs: list[dict[str, str]] | list[EmbeddingItem],
    *,
    model: str,
    cache_path: str | Path,
    dimensions: int | None = None,
) -> PlannedEmbeddingBatch:
    items = _normalize_embedding_inputs(inputs)
    cache = EmbeddingCache(cache_path)
    cached_results: dict[str, dict[str, Any]] = {}
    misses: list[EmbeddingItem] = []
    cache_warning = False

    for item in items:
        text_hash = compute_text_hash(item.id, item.text)
        try:
            cached_embedding = cache.get(
                item_id=item.id,
                text_hash=text_hash,
                model=model,
                dimensions=dimensions,
            )
        except CacheError:
            cache_warning = True
            cached_embedding = None

        if cached_embedding is None:
            misses.append(item)
            continue

        cached_results[item.id] = {
            "id": item.id,
            "text_hash": text_hash,
            "embedding": cached_embedding,
            "model": model,
            "dimensions": len(cached_embedding),
            "cached": True,
        }

    miss_tokens = sum(count_tokens(item.text) for item in misses)
    usage = EmbeddingBatchUsage(
        model=model,
        request_count=1 if misses else 0,
        input_count=len(items),
        cached_count=len(items) - len(misses),
        miss_count=len(misses),
        input_tokens=miss_tokens,
        estimated_cost_usd=estimate_embedding_cost_usd(model=model, input_tokens=miss_tokens),
    )
    return PlannedEmbeddingBatch(
        items=items,
        cached_results=cached_results,
        misses=misses,
        cache_warning=cache_warning,
        usage=usage,
    )


def execute_planned_embedding_batch(
    plan: PlannedEmbeddingBatch,
    *,
    model: str,
    cache_path: str | Path,
    dimensions: int | None = None,
    client: Any | None = None,
) -> tuple[list[dict[str, Any]], bool, EmbeddingBatchUsage]:
    cache = EmbeddingCache(cache_path)
    results = dict(plan.cached_results)
    cache_warning = plan.cache_warning
    usage = EmbeddingBatchUsage(
        model=plan.usage.model,
        request_count=plan.usage.request_count,
        input_count=plan.usage.input_count,
        cached_count=plan.usage.cached_count,
        miss_count=plan.usage.miss_count,
        input_tokens=plan.usage.input_tokens,
        estimated_cost_usd=plan.usage.estimated_cost_usd,
    )

    if plan.misses:
        embeddings, input_tokens = _request_embeddings(
            plan.misses,
            model=model,
            dimensions=dimensions,
            client=client,
        )
        usage.input_tokens = input_tokens
        usage.estimated_cost_usd = estimate_embedding_cost_usd(model=model, input_tokens=input_tokens)
        for item, embedding in zip(plan.misses, embeddings, strict=True):
            text_hash = compute_text_hash(item.id, item.text)
            try:
                cache.set(
                    item_id=item.id,
                    text_hash=text_hash,
                    model=model,
                    embedding=embedding,
                    dimensions=dimensions,
                )
            except CacheError:
                cache_warning = True

            results[item.id] = {
                "id": item.id,
                "text_hash": text_hash,
                "embedding": embedding,
                "model": model,
                "dimensions": len(embedding),
                "cached": False,
            }

    ordered = [results[item.id] for item in plan.items]
    return ordered, cache_warning, usage


def embed_batch(
    inputs: list[dict[str, str]] | list[EmbeddingItem],
    *,
    model: str,
    cache_path: str | Path,
    dimensions: int | None = None,
    client: Any | None = None,
) -> tuple[list[dict[str, Any]], bool, EmbeddingBatchUsage]:
    plan = plan_embedding_batch(
        inputs,
        model=model,
        cache_path=cache_path,
        dimensions=dimensions,
    )
    return execute_planned_embedding_batch(
        plan,
        model=model,
        cache_path=cache_path,
        dimensions=dimensions,
        client=client,
    )
