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
        "-p", "--prompt",
        help="Non-interactive mode: run one query and exit. "
             "Pipe stdin to provide context.",
    )

    # Workflow subparser — delegated to workflow module
    from .app.workflow.args import attach_workflow_parser
    attach_workflow_parser(parser)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def read_stdin_if_piped() -> str | None:
    """Read stdin if it is piped (not a TTY). Returns None otherwise."""
    if sys.stdin.isatty():
        return None
    return sys.stdin.read()
