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
        _run_app(interactive=False, prompt=args.prompt, stdin_text=read_stdin_if_piped())
    else:
        _run_app(interactive=True)


def _run_app(*, interactive: bool, prompt: str | None = None, stdin_text: str | None = None):
    """Unified entry: create CLIApp, check config, dispatch to run or run_oneshot."""
    from .app.cli import CLIApp

    app = CLIApp(interactive=interactive)
    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)

    if interactive:
        app.run()
    else:
        app.run_oneshot(prompt, stdin_text)


if __name__ == "__main__":
    main()
