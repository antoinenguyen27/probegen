from __future__ import annotations

import json
from pathlib import Path

import click

from parity.errors import EmbeddingError
from parity.tools.embedding import embed_batch
from parity.tools.similarity import classify_embeddings_against_corpus


@click.command("find-similar-batch", help="Find duplicate or boundary-adjacent eval cases for a scoped batch of candidates.")
@click.option("--candidates", "candidates_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--corpus", "corpus_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--duplicate-threshold", default=0.88, show_default=True, type=float)
@click.option("--boundary-threshold", default=0.72, show_default=True, type=float)
@click.option("--model", default="text-embedding-3-small", show_default=True)
@click.option("--cache", "cache_path", default=".parity/embedding_cache.db", show_default=True, type=click.Path(path_type=Path))
@click.option("--dimensions", type=int, default=None)
def find_similar_batch_command(
    candidates_path: Path,
    corpus_path: Path,
    output_path: Path,
    duplicate_threshold: float,
    boundary_threshold: float,
    model: str,
    cache_path: Path,
    dimensions: int | None,
) -> None:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    try:
        embedded_candidates, _ = embed_batch(
            candidates,
            model=model,
            cache_path=cache_path,
            dimensions=dimensions,
        )
    except EmbeddingError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    results = classify_embeddings_against_corpus(
        embedded_candidates,
        corpus,
        duplicate_threshold=duplicate_threshold,
        boundary_threshold=boundary_threshold,
    )
    payload = {
        "candidate_count": len(results),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    find_similar_batch_command()
