from __future__ import annotations

import click

from probegen.cli.embed_batch import embed_batch_command
from probegen.cli.find_similar import find_similar_command
from probegen.cli.get_behavior_diff import get_behavior_diff_command
from probegen.cli.init_cmd import init_command
from probegen.cli.post_comment import post_comment_command
from probegen.cli.resolve_run_id import resolve_run_id_command
from probegen.cli.run_stage import run_stage_command
from probegen.cli.setup_mcp import setup_mcp_command
from probegen.cli.write_probes import write_probes_command


@click.group()
def cli() -> None:
    """Probegen command group."""


cli.add_command(embed_batch_command)
cli.add_command(find_similar_command)
cli.add_command(get_behavior_diff_command)
cli.add_command(init_command)
cli.add_command(post_comment_command)
cli.add_command(resolve_run_id_command)
cli.add_command(run_stage_command)
cli.add_command(setup_mcp_command)
cli.add_command(write_probes_command)


if __name__ == "__main__":
    cli()
