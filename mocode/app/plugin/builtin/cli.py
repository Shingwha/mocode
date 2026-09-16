"""cli plugin — the interactive shell: display hook and REPL commands."""

from __future__ import annotations

from ..base import Plugin
from ..context import HostContext


class CLIPlugin(Plugin):
    name = "cli"
    description = "Interactive terminal UI: display hook and REPL commands"

    def build(self, ctx: HostContext) -> None:
        if not ctx.interactive or ctx.display is None:
            return

        # Imported here so a non-interactive run never pays for questionary.
        from ...cli.commands import misc, model, session
        from ...cli.hook import CLIDisplayHook

        ctx.hooks.append(CLIDisplayHook(ctx.display, ctx.tools))
        for module in (misc, model, session):
            ctx.register(*module.commands)


PLUGIN = CLIPlugin()
