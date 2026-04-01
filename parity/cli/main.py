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
from parity.cli.write_evals import post_write_comment_command, write_evals_command


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Parity — discover, analyze, and synthesize native evals for every AI change."""


cli.add_command(doctor_command)
cli.add_command(init_command)
cli.add_command(run_stage_command)
cli.add_command(setup_mcp_command)
cli.add_command(write_evals_command)

# CI/internal plumbing — callable but not listed in --help
for _cmd in [
    embed_batch_command,
    find_similar_batch_command,
    find_similar_command,
    get_behavior_diff_command,
    post_comment_command,
    post_write_comment_command,
    resolve_run_id_command,
]:
    _cmd.hidden = True
    cli.add_command(_cmd)


if __name__ == "__main__":
    cli()
