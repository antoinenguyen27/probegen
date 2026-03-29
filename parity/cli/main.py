from __future__ import annotations

import click

from parity import __version__
from parity.cli.doctor_cmd import doctor_command
from parity.cli.embed_batch import embed_batch_command
from parity.cli.find_similar_batch import find_similar_batch_command
from parity.cli.find_similar import find_similar_command
from parity.cli.get_behavior_diff import get_behavior_diff_command
from parity.cli.init_cmd import init_command
from parity.cli.post_comment import post_comment_command
from parity.cli.resolve_run_id import resolve_run_id_command
from parity.cli.run_stage import run_stage_command
from parity.cli.setup_mcp import setup_mcp_command
from parity.cli.write_probes import write_probes_command


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Parity — automatically generate evals for every AI change."""


cli.add_command(doctor_command)
cli.add_command(embed_batch_command)
cli.add_command(find_similar_batch_command)
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
