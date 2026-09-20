"""CLI argument parsing — runs before any framework imports."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocode",
        description="MoCode — Lean agent framework",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        help="Non-interactive mode: run one query and exit. "
        "Pipe stdin to provide context.",
    )

    commands = parser.add_subparsers(dest="command")
    plugin = commands.add_parser("plugin", help="Manage plugins")
    plugin_commands = plugin.add_subparsers(dest="plugin_command", required=True)

    install = plugin_commands.add_parser(
        "install", help="Install a plugin from a git URL or a local directory"
    )
    install.add_argument("source", help="git URL or path to a plugin directory")
    install.add_argument(
        "--project",
        action="store_true",
        help="install into ./.mocode/plugins instead of ~/.mocode/plugins",
    )

    sync = plugin_commands.add_parser(
        "sync", help="Materialise a plugin's own environment (uv sync)"
    )
    sync.add_argument("name", help="plugin name, as `plugin list` shows it")

    plugin_commands.add_parser("list", help="List the plugins of this project")

    remove = plugin_commands.add_parser("remove", help="Remove an installed plugin")
    remove.add_argument("name", help="plugin name, as `plugin list` shows it")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def read_stdin_if_piped() -> str | None:
    """Read stdin if it is piped (not a TTY). Returns None otherwise."""
    if sys.stdin.isatty():
        return None
    return sys.stdin.read()
