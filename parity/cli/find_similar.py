from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from parity.errors import EmbeddingError
from parity.tools.embedding import embed_batch
from parity.tools.similarity import classify_similarity, cosine_similarity


@click.command("find-similar", help="Find duplicate or boundary-adjacent eval cases.")
@click.option("--candidate", "candidate_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--corpus", "corpus_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--duplicate-threshold", default=0.88, show_default=True, type=float)
@click.option("--boundary-threshold", default=0.72, show_default=True, type=float)
@click.option("--model", default="text-embedding-3-small", show_default=True)
@click.option("--cache", "cache_path", default=".parity/embedding_cache.db", show_default=True, type=click.Path(path_type=Path))
@click.option("--dimensions", type=int, default=None)
def find_similar_command(
    candidate_path: Path,
    corpus_path: Path,
    output_path: Path,
    duplicate_threshold: float,
    boundary_threshold: float,
    model: str,
    cache_path: Path,
    dimensions: int | None,
) -> None:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    try:
        embedded_candidate, _ = embed_batch(
            [candidate],
            model=model,
            cache_path=cache_path,
            dimensions=dimensions,
        )
    except EmbeddingError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    candidate_embedding = embedded_candidate[0]["embedding"]
    results: list[dict[str, Any]] = []
    for item in corpus:
        score = cosine_similarity(candidate_embedding, item["embedding"])
        results.append(
            {
                "corpus_id": item["id"],
                "similarity": score,
                "classification": classify_similarity(
                    score,
                    duplicate_threshold=duplicate_threshold,
                    boundary_threshold=boundary_threshold,
                ),
            }
        )

    results.sort(key=lambda item: item["similarity"], reverse=True)
    top_match = results[0] if results else None
    payload = {
        "candidate_id": candidate["id"],
        "results": results,
        "top_match": top_match,
        "max_similarity": top_match["similarity"] if top_match else 0.0,
        "overall_classification": top_match["classification"] if top_match else "novel",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    find_similar_command()
