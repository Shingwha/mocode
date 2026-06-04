"""MoCode 0.3 — CLI entry point.

Usage:
    mocode                                              Launch interactive CLI
    mocode -p "prompt"                                  Non-interactive oneshot
    mocode workflow run <name> [key=value...]            Run workflow (background)
    mocode workflow run <name> --fg [key=value...]       Run workflow (foreground)
    mocode workflow list                                 List available workflows
    mocode workflow show <name>                          Show DAG structure
    mocode workflow status [run_id]                      Check run status
    mocode workflow result [run_id]                      Print full results
    mocode workflow stop <run_id>                        Kill background workflow
    mocode workflow runs                                 List recent runs
"""

from __future__ import annotations

import sys

from .cli_args import parse_args, read_stdin_if_piped


def main():
    args = parse_args()

    if getattr(args, "command", None) == "workflow":
        from .app.workflow.cli import run_cli

        run_cli(args)
    elif args.prompt is not None:
        _run_app(
            interactive=False, prompt=args.prompt, stdin_text=read_stdin_if_piped()
        )
    else:
        _run_app(interactive=True)


def _run_app(
    *, interactive: bool, prompt: str | None = None, stdin_text: str | None = None
):
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
