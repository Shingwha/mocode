"""The mcp tool builders — one mocode Tool per server tool, plus mcp_status.

A server tool becomes a plain :class:`~mocode.core.tool.Tool`: its MCP
``inputSchema`` passes through untouched as the argument schema, its calls
go through the session, and its result is split the way mocode wants —
text for the model in ``content``, facts for everything else in
``details``. Error mapping (decision D8): an ``isError`` result raises
:class:`~mocode.core.tool.ToolError` (``mcp_error``), an ``input_required``
answer is an ``McpError`` from the session and becomes
``mcp_input_required``, and a transport, timeout or protocol failure keeps
the session's category. ``mcp_status`` is the anchor the plugin hangs the
whole runtime on — the registry is the per-conversation handle, so
``close()`` finds the runtime again through ``ctx.tools.get("mcp_status").mcp_runtime``.

A server that declares the ``resources`` capability also gets three
read-only tools (:func:`resource_tools`), built on the session's resource
pass-throughs. They are one set per conversation rather than one per server —
the ``server`` argument picks the server, not the tool name — which is why
the runtime registers and unregisters them together as that set of servers
changes.
"""

from __future__ import annotations

import base64
import os
import tempfile
from typing import TYPE_CHECKING, Any

from .....core.tool import Tool, ToolError, ToolResult
from .client import McpError, McpSession
from .naming import normalize, tool_full_name

if TYPE_CHECKING:
    from .....core.hook import ToolCallContext
    from .runtime import McpRuntime

#: Used when a server tool declares no inputSchema at all.
_FALLBACK_SCHEMA = {"type": "object", "properties": {}}

#: The three read-only resource tools, registered and unregistered as a set
#: (decision D1). One set per conversation — the ``server`` argument picks
#: the server, so they are not per-server tools.
RESOURCE_TOOL_NAMES = (
    "list_mcp_resources",
    "list_mcp_resource_templates",
    "read_mcp_resource",
)


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
    session: McpSession,
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
            raise ToolError(str(e), e.code or "mcp_error")
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


# ── the resource tools ──────────────────────────────────────


def _resource_session(
    runtime: "McpRuntime", name: object
) -> "tuple[str, McpSession]":
    """The session one of the resource tools talks to.

    *name* selects a server by its configured name (folded or verbatim);
    omitted it must be the only connected server that declares the
    resources capability, and several of them is an error — the caller has
    to say which one (decision D1).
    """
    servers = runtime.resource_sessions()
    if name is not None and name != "":
        if not isinstance(name, str):
            raise ToolError("server must be a string", "mcp_error")
        wanted = normalize(name)
        for _key, cfg, session in servers:
            if wanted == normalize(cfg.name) or cfg.name == name:
                return cfg.name, session
        raise ToolError(
            f"no connected MCP server {name!r} exposes resources", "mcp_error"
        )
    if not servers:
        raise ToolError("no connected MCP server exposes resources", "mcp_error")
    if len(servers) > 1:
        listed = ", ".join(sorted(cfg.name for _key, cfg, _s in servers))
        raise ToolError(
            f"several servers expose resources ({listed}) — pass 'server'",
            "mcp_error",
        )
    return servers[0][1].name, servers[0][2]


def _mime_suffix(mime: object) -> str:
    """A filename suffix for a mime type — its subtype when that is a safe
    token, else nothing."""
    if not isinstance(mime, str) or "/" not in mime:
        return ""
    subtype = mime.split("/", 1)[1].split("+")[0].split(";")[0].strip().lower()
    if subtype.isalnum() and 1 <= len(subtype) <= 8:
        return f".{subtype}"
    return ""


def _spool_blob(data: bytes, mime: object) -> str:
    """Write *data* to a temp file and return its path.

    A tool result dies with the turn but whatever opens a binary — a viewer,
    a shell, a later tool — needs the bytes to outlive it, so the file is
    left in the OS temp directory for the process to clean up (decision D4).
    """
    fd, path = tempfile.mkstemp(prefix="mocode-mcp-", suffix=_mime_suffix(mime))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except BaseException:
        os.unlink(path)
        raise
    return path


def _decoded_blob(blob: object) -> "bytes | None":
    """The bytes behind a wire ``blob`` — None when there is no usable one."""
    if not isinstance(blob, str) or not blob:
        return None
    try:
        return base64.b64decode(blob, validate=True)
    except (ValueError, TypeError):
        return None


def _resource_list_tool(
    runtime: "McpRuntime", *, templates: bool, availability: str
) -> Tool:
    """One of the two listing tools — resources or resource templates."""

    async def run(args: dict) -> ToolResult:
        server_name, session = _resource_session(runtime, args.get("server"))
        cursor = args.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ToolError("cursor must be a string", "mcp_error")
        try:
            if templates:
                page = await session.list_resource_templates(cursor=cursor)
            else:
                page = await session.list_resources(cursor=cursor)
        except McpError as e:
            raise ToolError(str(e), e.code or "mcp_error")
        wire_key = "resourceTemplates" if templates else "resources"
        entries = page.get(wire_key)
        entries = entries if isinstance(entries, list) else []
        lines = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            uri = entry.get("uri") or entry.get("uriTemplate") or ""
            line = f"{entry.get('name') or uri}: {uri}"
            mime = entry.get("mimeType")
            if mime:
                line += f" [{mime}]"
            if entry.get("description"):
                line += f" — {entry['description']}"
            lines.append(line)
        noun = "resource templates" if templates else "resources"
        content = "\n".join(lines) if lines else f"no {noun}"
        details: dict[str, Any] = {
            "server": server_name,
            wire_key: entries,
            "is_error": False,
        }
        next_cursor = page.get("nextCursor")
        if next_cursor:
            details["nextCursor"] = next_cursor
            content += "\n(more results — pass this nextCursor back as cursor)"
        return ToolResult(content, details)

    if templates:
        name = "list_mcp_resource_templates"
        description = (
            "List the resource templates one MCP server exposes — "
            "parameterized uris whose parameters the model may fill in and "
            "then read with read_mcp_resource."
        )
    else:
        name = "list_mcp_resources"
        description = (
            "List the resources one MCP server exposes — name, uri, mime "
            "type and description, one page at a time."
        )
    return Tool(
        name=name,
        description=description,
        schema={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": (
                        "Which MCP server to list — omit it when only one "
                        "exposes resources."
                    ),
                },
                "cursor": {
                    "type": "string",
                    "description": (
                        "Pagination cursor — the nextCursor a previous "
                        "listing returned."
                    ),
                },
            },
        },
        func=run,
        tags=frozenset({"mcp"}),
        availability=availability,
    )


def _read_resource_tool(runtime: "McpRuntime", availability: str) -> Tool:
    """Read one resource — text stays text, an image lands in the details,
    any other binary is spooled to a temp file (decision D4)."""

    async def run(args: dict) -> ToolResult:
        server_name, session = _resource_session(runtime, args.get("server"))
        uri = args.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ToolError("uri is required", "mcp_error")
        try:
            result = await session.read_resource(uri)
        except McpError as e:
            raise ToolError(str(e), e.code or "mcp_error")
        blocks = result.get("contents")
        blocks = blocks if isinstance(blocks, list) else []
        texts: list[str] = []
        images: list[dict] = []
        files: list[dict] = []
        others: list[Any] = []
        mime: str | None = None
        for block in blocks:
            if not isinstance(block, dict):
                others.append(block)
                continue
            block_mime = block.get("mimeType")
            if mime is None and isinstance(block_mime, str):
                mime = block_mime
            blob = _decoded_blob(block.get("blob"))
            if blob is not None:
                if isinstance(block_mime, str) and block_mime.startswith("image/"):
                    images.append(
                        {
                            "type": "image",
                            "data": block.get("blob"),
                            "mimeType": block_mime,
                        }
                    )
                    texts.append(f"[image: {block_mime}]")
                else:
                    path = _spool_blob(blob, block_mime)
                    size = len(blob)
                    files.append(
                        {
                            "uri": block.get("uri"),
                            "path": path,
                            "size": size,
                            "mimeType": block_mime,
                        }
                    )
                    texts.append(
                        f"[file: {path} ({size} bytes, {block_mime or 'unknown'})]"
                    )
                continue
            text = block.get("text")
            if isinstance(text, str):
                texts.append(text)
            else:
                others.append(block)
        content = "\n".join(texts) if texts else f"[no content in {uri}]"
        details: dict[str, Any] = {
            "server": server_name,
            "uri": uri,
            "mimeType": mime,
            "content": others,
            "is_error": False,
        }
        if images:
            details["images"] = images
        if files:
            details["files"] = files
        return ToolResult(content, details)

    return Tool(
        name="read_mcp_resource",
        description=(
            "Read one MCP resource by uri. Text comes back as text; an image "
            "lands in the result's details as image data; any other binary is "
            "written to a temporary file whose path, size and mime type the "
            "result carries."
        ),
        schema={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": (
                        "Which MCP server exposes the resource — omit it when "
                        "only one does."
                    ),
                },
                "uri": {
                    "type": "string",
                    "description": (
                        "The resource uri, or a template uri with its "
                        "parameters filled in."
                    ),
                },
            },
            "required": ["uri"],
        },
        func=run,
        tags=frozenset({"mcp"}),
        availability=availability,
    )


def resource_tools(runtime: "McpRuntime", availability: str) -> list[Tool]:
    """The three read-only resource tools (decision D1) — the listing pair
    and the reader, offered while any connected server declares the
    resources capability."""
    return [
        _resource_list_tool(runtime, templates=False, availability=availability),
        _resource_list_tool(runtime, templates=True, availability=availability),
        _read_resource_tool(runtime, availability),
    ]
