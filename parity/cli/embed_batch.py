from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from parity.errors import EmbeddingError
from parity.tools.embedding import embed_batch


@click.command("embed-batch", help="Generate embeddings for a batch of eval inputs.")
@click.option("--inputs", "inputs_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--model", default="text-embedding-3-small", show_default=True)
@click.option("--cache", "cache_path", default=".parity/embedding_cache.db", show_default=True, type=click.Path(path_type=Path))
@click.option("--dimensions", type=int, default=None)
def embed_batch_command(
    inputs_path: Path,
    output_path: Path,
    model: str,
    cache_path: Path,
    dimensions: int | None,
) -> None:
    payload = json.loads(inputs_path.read_text(encoding="utf-8"))
    try:
        embeddings, cache_warning = embed_batch(
            payload,
            model=model,
            cache_path=cache_path,
            dimensions=dimensions,
        )
    except EmbeddingError as exc:
        raise SystemExit(_emit_error(str(exc), 1)) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(embeddings, indent=2), encoding="utf-8")
    if cache_warning:
        click.echo(
            "parity embed-batch: embedding cache warning — some cache reads or writes failed; "
            "embeddings are still valid and have been written to the output file.",
            err=True,
        )
    raise SystemExit(0)


def _emit_error(message: str, code: int) -> int:
    click.echo(message, err=True)
    return code


if __name__ == "__main__":
    embed_batch_command()
