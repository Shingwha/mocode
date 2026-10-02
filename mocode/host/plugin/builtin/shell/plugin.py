"""The shell plugin — one session per conversation, killed on close."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...base import Plugin
from ...context import BuildContext
from .session import BashSession
from .tool import bash_output_tool, bash_tool_for, kill_shell_tool

if TYPE_CHECKING:
    from ...context import HostContext


class ShellPlugin(Plugin):
    name = "shell"
    description = "Run shell commands in a persistent bash session"

    def build(self, ctx: BuildContext) -> None:
        # The session's working directory is the conversation's project: build()
        # runs once per conversation, so two projects never share one shell.
        # The context is kept so background jobs can report early output and
        # announce completion — it grows into a HostContext at assembly.
        session = BashSession(cwd=ctx.cwd, host=ctx)
        session.configure(ctx.plugin_config("shell"))
        ctx.tools.register(
            bash_tool_for(session, default_timeout=ctx.config.agent.tool_timeout)
        )
        ctx.tools.register(bash_output_tool(session))
        ctx.tools.register(kill_shell_tool(session))

    def close(self, ctx: HostContext) -> None:
        # The registry is the per-conversation handle to what build() created;
        # killing through it keeps the plugin instance stateless. The host
        # isolates a failure here from every other plugin's close().
        bash = ctx.tools.get("bash")
        session = getattr(bash, "session", None)
        if session is not None:
            session.shutdown()


PLUGIN = ShellPlugin()
