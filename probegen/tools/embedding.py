from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai import OpenAI

from probegen.errors import CacheError, EmbeddingError


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
) -> list[list[float]]:
    openai_client = client or OpenAI()
    kwargs: dict[str, Any] = {"model": model, "input": [item.text for item in items]}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions

    try:
        response = openai_client.embeddings.create(**kwargs)
    except Exception as exc:
        raise EmbeddingError(f"Embedding API error: {exc}") from exc

    return [list(record.embedding) for record in response.data]


def embed_batch(
    inputs: list[dict[str, str]] | list[EmbeddingItem],
    *,
    model: str,
    cache_path: str | Path,
    dimensions: int | None = None,
    client: Any | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    items = [
        item if isinstance(item, EmbeddingItem) else EmbeddingItem(id=item["id"], text=item["text"])
        for item in inputs
    ]
    cache = EmbeddingCache(cache_path)
    results: dict[str, dict[str, Any]] = {}
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

        results[item.id] = {
            "id": item.id,
            "text_hash": text_hash,
            "embedding": cached_embedding,
            "model": model,
            "dimensions": len(cached_embedding),
            "cached": True,
        }

    if misses:
        embeddings = _request_embeddings(misses, model=model, dimensions=dimensions, client=client)
        for item, embedding in zip(misses, embeddings, strict=True):
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

    ordered = [results[item.id] for item in items]
    return ordered, cache_warning
