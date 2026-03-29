from __future__ import annotations

from pathlib import Path

from parity.tools.embedding import EmbeddingCache, embed_batch


class _FakeEmbeddingRecord:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _FakeEmbeddingResponse:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.data = [_FakeEmbeddingRecord(embedding) for embedding in embeddings]


class _FakeEmbeddingsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.embeddings = self

    def create(self, *, model: str, input: list[str], dimensions: int | None = None):
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        return _FakeEmbeddingResponse([[float(index), float(index + 1)] for index, _ in enumerate(input)])


def test_embedding_cache_round_trip(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "cache.db")
    cache.set(
        item_id="case_1",
        text_hash="sha256:abc",
        model="text-embedding-3-small",
        embedding=[0.1, 0.2],
        dimensions=None,
    )

    cached = cache.get(
        item_id="case_1",
        text_hash="sha256:abc",
        model="text-embedding-3-small",
        dimensions=None,
    )

    assert cached == [0.1, 0.2]


def test_embed_batch_uses_cache_after_first_call(tmp_path: Path) -> None:
    client = _FakeEmbeddingsClient()
    inputs = [
        {"id": "case_1", "text": "What changed?"},
        {"id": "case_2", "text": "Tell me more."},
    ]

    first_results, first_warning, first_usage = embed_batch(
        inputs,
        model="text-embedding-3-small",
        cache_path=tmp_path / "cache.db",
        client=client,
    )
    second_results, second_warning, second_usage = embed_batch(
        inputs,
        model="text-embedding-3-small",
        cache_path=tmp_path / "cache.db",
        client=client,
    )

    assert first_warning is False
    assert second_warning is False
    assert len(client.calls) == 1
    assert [item["cached"] for item in first_results] == [False, False]
    assert [item["cached"] for item in second_results] == [True, True]
    assert first_usage.request_count == 1
    assert first_usage.miss_count == 2
    assert first_usage.estimated_cost_usd is not None
    assert second_usage.request_count == 0
    assert second_usage.cached_count == 2
    assert second_usage.estimated_cost_usd == 0.0
