"""MoCode 0.3 — CLI entry point.

Usage:
    mocode              Launch interactive CLI
    mocode -p "prompt"  Non-interactive: pipe stdin as context, print response, exit
"""

from __future__ import annotations

import sys

from .cli_args import parse_args, read_stdin_if_piped


def main():
    args = parse_args()

    if args.prompt is not None:
        _run_oneshot(args)
    else:
        _run_interactive()


def _run_oneshot(args):
    """Non-interactive path: load config, run agent, print, exit."""
    from .app.cli import CLIApp

    app = CLIApp(interactive=False)
    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)

    stdin_text = read_stdin_if_piped()
    app.run_oneshot(args.prompt, stdin_text)


def _run_interactive():
    """Interactive path: existing CLIApp flow."""
    from .app.cli import CLIApp

    app = CLIApp()
    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)
    app.run()


if __name__ == "__main__":
    main()
