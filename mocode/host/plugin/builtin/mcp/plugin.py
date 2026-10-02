"""The mcp builtin plugin — the lifecycle and the prompt section.

``build()`` is cheap: it parses the configuration and registers the
``mcp_status`` anchor tool with the conversation's
:class:`~mocode.host.plugin.builtin.mcp.runtime.McpRuntime` hung on it.
``prepare()`` does the I/O — connecting servers, direct ones under a
bounded wait — and appends the ``mcp_servers`` prompt section. ``close()``
finds the runtime again through the registry — the per-conversation handle,
so the plugin instance stays stateless — and kills the children. The
prompt section is deliberately not pinned: it renders once at
materialization like any live section, and cache-protect diffs it against
the registry through ``derived_from="tools"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from .....core.prompt import Section
from ...base import Plugin
from .naming import resolve_server_exposure
from .runtime import McpRuntime
from .tools import mcp_status_tool

if TYPE_CHECKING:
    from ...context import BuildContext, HostContext


def _render_mcp_servers(
    runtime: McpRuntime,
) -> Callable[[dict[str, Any]], str]:
    """The mcp_servers section — one line per reachable server.

    Every enabled server whose effective exposure is not ``hidden``: its
    namespace, how its tools are reached (``direct`` to the model,
    ``codemode`` otherwise) and a one-line description — the configured one,
    else the first line of the server instructions once connected. No
    servers → an empty render, and the prompt skips the section.
    """

    def render(builder_context: dict[str, Any]) -> str:
        lines = []
        for key, cfg in runtime.config.items():
            if not cfg.enabled:
                continue
            exposure = resolve_server_exposure(cfg, runtime.default_exposure)
            if exposure == "hidden":
                continue
            how = "direct" if exposure == "direct" else "codemode"
            description = cfg.description
            if not description:
                session = runtime.sessions.get(key)
                if session is not None and session.instructions:
                    description = session.instructions.splitlines()[0]
            line = f"- {cfg.name}: {how}"
            if description:
                line += f" — {description}"
            lines.append(line)
        return "\n".join(lines)

    return render


class McpPlugin(Plugin):
    """Connect to MCP servers and expose their tools."""

    name = "mcp"
    description = "Connect to MCP servers and expose their tools"

    def build(self, ctx: "BuildContext") -> None:
        """Parse configuration and register the anchor tool.

        No connections here — build() is synchronous and cheap; the runtime
        only prepares what prepare() will need.
        """
        runtime = McpRuntime(ctx)
        ctx.tools.register(mcp_status_tool(runtime))

    async def prepare(self, ctx: "HostContext") -> None:
        """Connect servers (bounded wait for direct ones), then expose the
        server table to the prompt."""
        runtime = ctx.tools.get("mcp_status").mcp_runtime
        await runtime.start()
        ctx.prompt_sections.append(
            Section(
                name="mcp_servers",
                render=_render_mcp_servers(runtime),
                priority=46,
                derived_from="tools",
            )
        )

    def close(self, ctx: "HostContext") -> None:
        """Kill this conversation's servers — found again through the
        registry, never through plugin state."""
        status = ctx.tools.get("mcp_status")
        runtime = getattr(status, "mcp_runtime", None)
        if runtime is not None:
            runtime.shutdown()


PLUGIN = McpPlugin()
