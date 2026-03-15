from __future__ import annotations

import json
import os
from pathlib import Path

import click

from probegen.config import ProbegenConfig
from probegen.errors import ConfigError


def generate_mcp_config(config: ProbegenConfig, env: dict[str, str]) -> dict:
    servers: dict[str, dict] = {}

    if env.get("LANGSMITH_API_KEY") and config.platforms.langsmith:
        servers["langsmith"] = {
            "command": "uvx",
            "args": ["langsmith-mcp-server"],
            "env": {"LANGSMITH_API_KEY": env["LANGSMITH_API_KEY"]},
        }

    if env.get("BRAINTRUST_API_KEY") and config.platforms.braintrust:
        servers["braintrust"] = {
            "type": "http",
            "url": "https://api.braintrust.dev/mcp",
            "headers": {"Authorization": f"Bearer {env['BRAINTRUST_API_KEY']}"},
        }

    if env.get("PHOENIX_API_KEY") and config.platforms.arize_phoenix:
        servers["phoenix"] = {
            "command": "npx",
            "args": [
                "-y",
                "@arizeai/phoenix-mcp@latest",
                "--baseUrl",
                config.platforms.arize_phoenix.base_url,
                "--apiKey",
                env["PHOENIX_API_KEY"],
            ],
        }

    return {"mcpServers": servers}


@click.command("setup-mcp")
@click.option("--config", "config_path", default="probegen.yaml", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output", "output_path", default=".claude/mcp_servers.json", show_default=True, type=click.Path(dir_okay=False, path_type=Path))
def setup_mcp_command(config_path: Path, output_path: Path) -> None:
    try:
        config = ProbegenConfig.load(config_path, allow_missing=True)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    payload = generate_mcp_config(config, dict(os.environ))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    setup_mcp_command()
