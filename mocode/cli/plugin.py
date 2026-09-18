"""The CLI's plugin — the terminal's own contributions.

It goes through the same channel a third-party plugin does: register commands,
append a hook. Keeping it on that channel is what stops the terminal from
becoming a special case inside the host.
"""

from __future__ import annotations

from ..host.plugin.base import Plugin
from ..host.plugin.context import HostContext


class CLIPlugin(Plugin):
    name = "cli"
    description = "Terminal UI: renders the event stream and adds terminal commands"

    def build(self, ctx: HostContext) -> None:
        # Imported here so a headless run never pays for questionary.
        from .commands import misc, model, session

        for module in (misc, model, session):
            ctx.register(*module.commands)

        # Rendering needs the terminal itself, not the Frontend protocol:
        # incremental writes, styling and the input prompt are this frontend's
        # own business. Any other frontend just gets the commands.
        from .display import Display

        if isinstance(ctx.display, Display):
            from .hook import CLIDisplayHook

            ctx.hooks.append(CLIDisplayHook(ctx.display, ctx.tools))


PLUGIN = CLIPlugin()
