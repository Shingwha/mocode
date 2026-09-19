"""The CLI's plugin — the terminal's own slash commands.

It goes through the same channel a third-party plugin does, and contributes only
commands: the renderer is the frontend's own (``cli/app.py`` installs it on the
agent it built), because drawing a terminal is not something a plugin hands to
the host. Keeping the commands on this channel is what stops the terminal from
becoming a special case inside the host.
"""

from __future__ import annotations

from ..host.plugin.base import Plugin
from ..host.plugin.context import HostContext


class CLIPlugin(Plugin):
    name = "cli"
    description = "Terminal commands: /help /clear /copy /model /export /resume"

    def build(self, ctx: HostContext) -> None:
        # Imported here so a headless run never pays for questionary.
        from .commands import misc, model, session

        for module in (misc, model, session):
            ctx.register(*module.commands)


PLUGIN = CLIPlugin()
