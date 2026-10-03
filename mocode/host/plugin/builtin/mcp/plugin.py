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
from .client import STATE_CONNECTED
from .naming import resolve_server_exposure
from .runtime import McpRuntime
from .tools import mcp_status_tool

if TYPE_CHECKING:
    from ...context import BuildContext, HostContext

#: Names per server in the prompt list — past this, search_tools() takes over.
_PROMPT_TOOL_NAME_LIMIT = 30


def _render_mcp_servers(
    runtime: McpRuntime,
) -> Callable[[dict[str, Any]], str]:
    """The mcp_servers section — one line per reachable server, plus the
    tool-name catalogue of every connected one.

    Every enabled server whose effective exposure is not ``hidden``: its
    namespace, how its tools are reached (``direct`` to the model,
    ``codemode`` otherwise) and a one-line description — the configured one,
    else the first line of the server instructions once connected. A
    connected server additionally lists its tools' registered full names
    (``mcp__<server>__<tool>`` — the one spelling a script calls them by,
    and the catalogue's) on an indented continuation line — a catalogue,
    not a manual: usage stays with ``describe_tool()`` in a codemode
    script. Only callable names make the list — the program audience's
    projection of the registry drops ``hidden`` per-tool entries, never
    advertising a name the run would refuse. A server still connecting
    keeps the one-line form, and a list longer than
    ``_PROMPT_TOOL_NAME_LIMIT`` truncates with a ``search_tools()``
    pointer. No servers → an empty render, and the prompt skips the
    section.
    """

    def render(builder_context: dict[str, Any]) -> str:
        lines = []
        visible = set(runtime._ctx.tools.names(audience="program"))
        for key, cfg in runtime.config.items():
            if not cfg.enabled:
                continue
            exposure = resolve_server_exposure(cfg, runtime.default_exposure)
            if exposure == "hidden":
                continue
            how = "direct" if exposure == "direct" else "codemode"
            description = cfg.description
            session = runtime.sessions.get(key)
            if not description and session is not None and session.instructions:
                description = session.instructions.splitlines()[0]
            line = f"- {cfg.name}: {how}"
            if description:
                line += f" — {description}"
            lines.append(line)
            registered = runtime._registered.get(key, {})
            if session is not None and session.state == STATE_CONNECTED and registered:
                names = sorted(full for full in registered if full in visible)
                if not names:
                    continue
                if len(names) > _PROMPT_TOOL_NAME_LIMIT:
                    rest = len(names) - _PROMPT_TOOL_NAME_LIMIT
                    tail = (
                        f" … +{rest} more"
                        " (search_tools() in a codemode script)"
                    )
                    lines.append(
                        "  tools: "
                        + ", ".join(names[:_PROMPT_TOOL_NAME_LIMIT])
                        + tail
                    )
                else:
                    lines.append("  tools: " + ", ".join(names))
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
