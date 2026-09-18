"""MoCode 0.3 — CLI entry point.

Usage:
    mocode                                              Launch interactive CLI
    mocode -p "prompt"                                  Non-interactive oneshot
"""

from __future__ import annotations

import sys

from .cli_args import parse_args, read_stdin_if_piped


def main():
    args = parse_args()

    if args.prompt is not None:
        stdin_text = read_stdin_if_piped()
        prompt = args.prompt
        if prompt == "-" and stdin_text:
            # Special marker: read prompt from stdin
            prompt = stdin_text.rstrip()
            stdin_text = None
        _run_app(interactive=False, prompt=prompt, stdin_text=stdin_text)
    else:
        _run_app(interactive=True)


def _run_app(
    *, interactive: bool, prompt: str | None = None, stdin_text: str | None = None
):
    """Unified entry: create CLIApp, check config, dispatch to run or run_oneshot."""
    from .cli import CLIApp

    try:
        # A one-shot on a terminal draws its turn as it happens; redirected, it
        # prints only the answer. Interactive already implies a frontend.
        app = CLIApp(interactive=interactive, render=sys.stdout.isatty())
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)

    if interactive:
        app.run()
    else:
        app.run_oneshot(prompt, stdin_text)


if __name__ == "__main__":
    main()
