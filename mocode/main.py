"""MoCode 0.4 — CLI entry point.

Usage:
    mocode                                              Launch interactive CLI
    mocode -p "prompt"                                  Non-interactive oneshot
    mocode plugin install <git-url|path> [--project]    Install a plugin
    mocode plugin sync <name>                           Materialise its environment
    mocode plugin list                                  Show this project's plugins
    mocode plugin remove <name>                         Remove an installed plugin
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .cli_args import parse_args, read_stdin_if_piped

if TYPE_CHECKING:
    import argparse


def main():
    args = parse_args()

    if args.command == "plugin":
        sys.exit(_run_plugin(args))

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
    """Unified entry: create CLIApp, dispatch to run or run_oneshot."""
    from .cli import CLIApp

    try:
        # A one-shot on a terminal draws its turn as it happens; redirected, it
        # prints only the answer. Interactive already implies a frontend.
        app = CLIApp(interactive=interactive, render=sys.stdout.isatty())
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if interactive:
        app.run()
    else:
        app.run_oneshot(prompt, stdin_text)


def _run_plugin(args: "argparse.Namespace") -> int:
    """``mocode plugin …`` — manage plugins without starting a conversation.

    Glue only: parse what the subcommand needs, call the host layer, say what
    happened. The operations themselves live in
    :mod:`mocode.host.plugin.install`; a plugin installed or synced here is
    loaded by the *next* start — loading is not retried in a running process.
    """
    from pathlib import Path

    from .host.plugin import install as plugins
    from .host.plugin.host import default_plugin_dirs

    cwd = Path.cwd()
    home = Path.home() / ".mocode"
    dirs = default_plugin_dirs(cwd, home)

    try:
        if args.plugin_command == "install":
            root = cwd / ".mocode" / "plugins" if args.project else home / "plugins"
            installed = plugins.install_plugin(args.source, root=root)
            print(f"installed {installed.name} -> {installed.directory}")
            if installed.env_warning:
                print(f"warning: {installed.env_warning}", file=sys.stderr)
            print("Restart MoCode to load it.")
            return 0

        if args.plugin_command == "sync":
            print(plugins.sync_plugin(args.name, dirs=dirs))
            print("Restart MoCode to load it with its dependencies.")
            return 0

        if args.plugin_command == "remove":
            removed = plugins.remove_plugin(args.name, roots=dirs)
            print(f"removed {args.name} ({removed})")
            return 0

        notes = {"own env": "own environment", "declared": "needs sync"}
        for item in plugins.list_plugins(dirs):
            note = f"  [{notes[item.env]}]" if item.env in notes else ""
            print(f"{item.name:<24} {item.version or '-':<10} {item.source}{note}")
        return 0
    except plugins.PluginInstallError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    main()
