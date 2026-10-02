"""The mcp tool builders — one mocode Tool per server tool, plus mcp_status.

A server tool becomes a plain :class:`~mocode.core.tool.Tool`: its MCP
``inputSchema`` passes through untouched as the argument schema, its calls
go through the session, and its result is split the way mocode wants —
text for the model in ``content``, facts for everything else in
``details``. Error mapping (decision D8): an ``isError`` result raises
:class:`~mocode.core.tool.ToolError` (``mcp_error``), a modern
``input_required`` result is already an ``McpError`` from the session and
becomes ``mcp_input_required``, and transport/protocol failures become
``mcp_transport``. ``mcp_status`` is the anchor the plugin hangs the whole
runtime on — the registry is the per-conversation handle, so ``close()``
finds the runtime again through ``ctx.tools.get("mcp_status").mcp_runtime``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .....core.tool import Tool, ToolError, ToolResult
from .naming import normalize, tool_full_name
from .session import McpError, StdioSession

if TYPE_CHECKING:
    from .....core.hook import ToolCallContext
    from .runtime import McpRuntime

#: Used when a server tool declares no inputSchema at all.
_FALLBACK_SCHEMA = {"type": "object", "properties": {}}


def _split_content(content: list) -> "tuple[str, list[dict], list[Any]]":
    """Sort result blocks: text for the model, images and other blocks for
    the details. Images also leave a one-line placeholder in the text."""
    texts: list[str] = []
    images: list[dict] = []
    others: list[Any] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
        elif isinstance(block, dict) and block.get("type") == "image":
            images.append(block)
            texts.append(f"[image: {block.get('mimeType', 'unknown')}]")
        else:
            others.append(block)
    return "\n".join(texts), images, others


def mcp_tool(
    runtime: "McpRuntime",
    session: StdioSession,
    server_name: str,
    raw_tool: dict,
    availability: str,
    disabled: bool,
) -> Tool:
    """Wrap one MCP tool description as a mocode Tool.

    *name* is the collision-resolved full name — taken from the runtime's
    assignment when there is one. The raw MCP name is kept on
    ``tool.mcp_raw_name`` (call arguments use it) and both names on
    ``tool.mcp`` for anything that must reach the server identity.
    """
    raw_name = raw_tool.get("name", "") if isinstance(raw_tool, dict) else ""
    key = normalize(server_name)
    full_name = runtime.assignments.get(key, {}).get(raw_name) if runtime else None
    if not full_name:
        full_name = tool_full_name(key, raw_name)

    description = raw_tool.get("description") if isinstance(raw_tool, dict) else None
    if not isinstance(description, str) or not description:
        description = f"MCP tool {raw_name} from {server_name}"
    schema = raw_tool.get("inputSchema") if isinstance(raw_tool, dict) else None
    if not isinstance(schema, dict):
        schema = dict(_FALLBACK_SCHEMA)

    async def run(args: dict, ctx: "ToolCallContext | None" = None) -> ToolResult:
        try:
            result = await session.call_tool(raw_name, args)
        except McpError as e:
            raise ToolError(str(e), e.code or "mcp_transport")
        blocks = result.get("content")
        content = blocks if isinstance(blocks, list) else []
        text, images, others = _split_content(content)
        if result.get("isError"):
            raise ToolError(text or "MCP tool call failed", "mcp_error")
        details = {
            "server": server_name,
            "tool": raw_name,
            "content": others,
            "structured_content": result.get("structuredContent"),
            "is_error": False,
        }
        if images:
            details["images"] = images
        return ToolResult(text, details)

    tool = Tool(
        name=full_name,
        description=description,
        schema=schema,
        func=run,
        tags=frozenset({"mcp", f"mcp:{key}"}),
        with_context=True,
        availability=availability,
    )
    tool.mcp = {"server": server_name, "tool": raw_name}  # type: ignore[attr-defined]
    tool.mcp_raw_name = raw_name  # type: ignore[attr-defined]
    return tool


def mcp_status_tool(runtime: "McpRuntime") -> Tool:
    """The anchor tool — program-only, holds the runtime for close() and
    answers codemode scripts asking what MCP servers exist."""

    async def run(args: dict) -> ToolResult:
        servers = runtime.status()
        lines = []
        for entry in servers:
            line = f"{entry['name']}: {entry['state']}"
            if entry["tools"]:
                line += f" ({entry['tools']} tools)"
            if entry["error"]:
                line += f" — {entry['error']}"
            lines.append(line)
        content = "\n".join(lines) if lines else "no MCP servers configured"
        return ToolResult(content, {"servers": servers})

    tool = Tool(
        name="mcp_status",
        description=(
            "Report the state of every configured MCP server — connected or "
            "not, how many tools each currently exposes, and the last error "
            "for a failed one."
        ),
        schema={"type": "object", "properties": {}},
        func=run,
        tags=frozenset({"mcp"}),
        availability="program",
    )
    tool.mcp_runtime = runtime  # type: ignore[attr-defined]
    return tool
