"""The mcp plugin's resource tools — three read-only tools over a server's
resources capability.

Every server here speaks to the plugin through the SDK's own in-process
seam, the way ``tests/test_builtin_mcp.py`` exercises the session: a
high-level ``MCPServer`` carries the ordinary shape (a text resource, an
image, a binary blob, a uri template), while a hand-built low-level
``Server`` carries the shapes the high-level one cannot — a paginated
listing, a legacy ``-32002`` error, and a server that declares no resources
capability at all. Each one is wired into a real ``McpRuntime`` through the
plugin's own ``on_connected`` path, so what is under test is the
registration the runtime itself performs, not a hand-registered tool.

No network, no subprocess: everything runs in this process, and every await
is bounded so a broken server can never hang the suite on Windows.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from mcp.server import MCPServer
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.shared.exceptions import MCPError
from mcp.types import (
    ListResourceTemplatesResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

from mocode.core.agent import AgentConfig
from mocode.core.dispatch import ToolDispatcher
from mocode.core.events import TOOL_DENIED
from mocode.core.hook import HookRunner
from mocode.core.tool import ToolError, ToolRegistry
from mocode.host.config import Config
from mocode.host.plugin.builtin.mcp.client import STATE_DISCONNECTED, McpSession
from mocode.host.plugin.builtin.mcp.runtime import McpRuntime
from mocode.host.plugin.builtin.mcp.tools import RESOURCE_TOOL_NAMES
from mocode.host.plugin.context import BuildContext

BOUND = 15  # seconds — every await in this file stays bounded

#: A 1x1 PNG — the image resource's bytes.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
#: Raw bytes no model should ever read as text — the blob resource's content.
BLOB = b"\x00\x01\x02\x03binary-payload"


async def _list_tools_empty(ctx: Any, params: Any) -> ListToolsResult:
    return ListToolsResult(tools=[])


# ── servers ──────────────────────────────────────────────────


def resource_server(name: str = "withres", note: str = "ship it") -> MCPServer:
    """A server with one resource of each of the three kinds — text, image
    (``image/*``) and an opaque binary — plus a uri template. *note* is the
    text resource's content, so two servers on one runtime stay apart."""
    server = MCPServer(name=name, version="1.2.3")

    @server.resource("note://today")
    def today() -> str:
        "today's note"

        return note

    @server.resource("pic://logo", mime_type="image/png")
    def logo() -> bytes:
        return PNG

    @server.resource("blob://data", mime_type="application/octet-stream")
    def blob() -> bytes:
        return BLOB

    @server.resource("greeting://{name}")
    def greeting(name: str) -> str:
        "a greeting"

        return f"hello {name}"

    return server


def bare_server(name: str = "plain-low") -> LowLevelServer:
    """A low-level server that declares no resources capability — the shape
    the resource tools must not register for."""
    return LowLevelServer(name, on_list_tools=_list_tools_empty)


async def _list_resources_paged(ctx: Any, params: Any) -> ListResourcesResult:
    """Two pages of resources: the first hands out a ``nextCursor``."""
    cursor = getattr(params, "cursor", None)
    if cursor == "page-2":
        return ListResourcesResult(
            resources=[{"uri": "note://second", "name": "second"}]
        )
    return ListResourcesResult(
        resources=[{"uri": "note://first", "name": "first"}],
        next_cursor="page-2",
    )


async def _list_templates_none(ctx: Any, params: Any) -> ListResourceTemplatesResult:
    return ListResourceTemplatesResult(resource_templates=[])


def paged_server() -> LowLevelServer:
    """A low-level server whose resource listing paginates — the cursor
    pass-through shape."""
    return LowLevelServer(
        "paged",
        on_list_tools=_list_tools_empty,
        on_list_resources=_list_resources_paged,
        on_list_resource_templates=_list_templates_none,
    )


async def _read_legacy_missing(ctx: Any, params: Any) -> ReadResourceResult:
    """The pre-2026-07-28 answer for an unknown resource: ``-32002``."""
    raise MCPError(code=-32002, message="resource not found (legacy)")


def legacy_server() -> LowLevelServer:
    """A low-level server that answers a read with the old error code —
    server_capabilities says resources, the protocol dialect says -32002."""
    return LowLevelServer(
        "legacy",
        on_list_tools=_list_tools_empty,
        on_list_resources=_list_resources_paged,
        on_list_resource_templates=_list_templates_none,
        on_read_resource=_read_legacy_missing,
    )


# ── the runtime seam ─────────────────────────────────────────


def stdio_entry(script: str | None = None, **extra) -> dict:
    """A stdio entry the config loader accepts — the in-process server that
    replaces it is never launched."""
    return {"command": "python", "args": ["-c", script or "pass"], **extra}


def make_runtime(
    tmp_path: Path,
    servers: dict,
    *,
    mcp_extra: dict | None = None,
    codemode_enabled: bool = False,
) -> McpRuntime:
    """A runtime over *servers* — every entry is a placeholder the test
    replaces with an in-process server through :func:`connect`."""
    plugins: dict[str, Any] = {"mcp": {"servers": servers, **(mcp_extra or {})}}
    if codemode_enabled:
        plugins["codemode"] = {"enabled": True}
    config = Config(provider="p", model="m", plugins=plugins)
    ctx = BuildContext(home=tmp_path / "home", cwd=tmp_path, config=config)
    return McpRuntime(ctx)


@pytest_asyncio.fixture
async def runtime_factory(tmp_path):
    """Build runtimes whose sessions are in-process servers; shut every
    session down on teardown."""
    created: list[McpRuntime] = []

    def factory(
        servers: dict, *, mcp_extra: dict | None = None, codemode_enabled: bool = False
    ) -> McpRuntime:
        runtime = make_runtime(
            tmp_path,
            servers,
            mcp_extra=mcp_extra,
            codemode_enabled=codemode_enabled,
        )
        created.append(runtime)
        return runtime

    yield factory
    for runtime in created:
        for session in runtime.sessions.values():
            try:
                await asyncio.wait_for(session.close(), BOUND)
            except Exception:
                session.shutdown()
        runtime.shutdown()


def _dispatcher(registry: ToolRegistry) -> ToolDispatcher:
    """The one execution path, bare — no hooks, events recorded nowhere."""

    async def publish(event, *, fold: bool) -> None:
        return None

    return ToolDispatcher(registry, HookRunner([]), AgentConfig(), publish)


async def connect(runtime: McpRuntime, key: str, server: object) -> McpSession:
    """Wire an in-process *server* into the runtime's slot for *key* and run
    the connect path the plugin runs — the runtime's own callbacks, so the
    registration under test is the one the plugin performs."""
    session = McpSession(
        runtime.config[key],
        server=server,
        on_connected=runtime._on_connected,
        on_tools_changed=runtime._on_tools_changed,
    )
    runtime.sessions[key] = session
    await asyncio.wait_for(session.connect_and_register(), BOUND)
    return session


# ── registration (decision D2) ───────────────────────────────


class TestRegistration:
    async def test_a_resource_server_registers_the_three_tools(
        self, runtime_factory
    ):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())

        registry = runtime._ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert name in registry, name
        read = registry.get("read_mcp_resource")
        listing = registry.get("list_mcp_resources")
        templates = registry.get("list_mcp_resource_templates")
        assert read.availability == "both"  # auto exposure → direct (no codemode)
        assert listing.availability == "both"
        assert templates.availability == "both"
        assert read.tags == frozenset({"mcp"})
        assert read.schema["required"] == ["uri"]
        assert "server" in read.schema["properties"]
        assert set(listing.schema["properties"]) == {"server", "cursor"}
        assert listing.schema.get("required") is None

    async def test_a_server_without_the_capability_registers_nothing(
        self, runtime_factory
    ):
        runtime = runtime_factory({"plain": stdio_entry()})
        session = await connect(runtime, "plain", bare_server())
        assert session.server_capabilities is not None
        assert session.server_capabilities.resources is None

        registry = runtime._ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert name not in registry
        # the per-server tool path is untouched — an empty listing registers nothing
        assert [n for n in registry.names() if n.startswith("mcp__")] == []

    async def test_reconciling_twice_keeps_the_same_tool_objects(
        self, runtime_factory
    ):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        first = runtime._ctx.tools.get("read_mcp_resource")

        runtime._apply_resource_tools()

        assert runtime._ctx.tools.get("read_mcp_resource") is first

    async def test_no_resource_server_left_and_the_tools_go_away(
        self, runtime_factory
    ):
        runtime = runtime_factory({"demo": stdio_entry()})
        session = await connect(runtime, "demo", resource_server())
        assert "read_mcp_resource" in runtime._ctx.tools

        session.state = STATE_DISCONNECTED
        runtime._apply_resource_tools()

        for name in RESOURCE_TOOL_NAMES:
            assert name not in runtime._ctx.tools

    async def test_a_late_resource_server_registers_on_its_connect(
        self, runtime_factory
    ):
        runtime = runtime_factory(
            {"plain": stdio_entry(), "demo": stdio_entry()}
        )
        await connect(runtime, "plain", bare_server())
        assert "read_mcp_resource" not in runtime._ctx.tools

        await connect(runtime, "demo", resource_server())
        assert "read_mcp_resource" in runtime._ctx.tools


# ── exposure (decision D3) ───────────────────────────────────


class TestExposure:
    async def test_the_default_exposure_offers_the_tools_to_the_model(
        self, runtime_factory
    ):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "both"

    async def test_codemode_keeps_them_program_only(self, runtime_factory):
        runtime = runtime_factory(
            {"demo": stdio_entry()}, codemode_enabled=True
        )
        await connect(runtime, "demo", resource_server())
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "program"

    async def test_a_configured_server_exposure_is_honoured(self, runtime_factory):
        runtime = runtime_factory(
            {"demo": stdio_entry(exposure="codemode")}
        )
        await connect(runtime, "demo", resource_server())
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "program"

    async def test_the_widest_exposure_wins(self, runtime_factory):
        runtime = runtime_factory(
            {
                "readonly": stdio_entry(exposure="codemode"),
                "direct": stdio_entry(exposure="direct"),
            }
        )
        await connect(runtime, "readonly", resource_server("readonly"))
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "program"

        await connect(runtime, "direct", resource_server("direct"))
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "both"

    async def test_a_hidden_server_registers_the_tools_switched_off(
        self, runtime_factory
    ):
        """A hidden server's resources stay invisible to both audiences —
        the same availability_for pipeline a hidden server tool takes."""
        runtime = runtime_factory({"demo": stdio_entry(exposure="hidden")})
        await connect(runtime, "demo", resource_server())

        registry = runtime._ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert registry.get(name) is not None  # registered, like a hidden tool
            assert name not in registry.names(audience="model")
            assert name not in registry.names(audience="program")

    async def test_a_switched_off_resource_tool_refuses_to_run(self, runtime_factory):
        """The dispatcher is the one execution path: a disabled tool's run is
        refused there, whatever the Tool object itself would do."""
        runtime = runtime_factory({"demo": stdio_entry(exposure="hidden")})
        await connect(runtime, "demo", resource_server())

        dispatcher = _dispatcher(runtime._ctx.tools)
        result = await asyncio.wait_for(
            dispatcher.run(
                "read_mcp_resource", {"uri": "note://today"}, origin="program"
            ),
            BOUND,
        )
        assert result.status == TOOL_DENIED
        assert "switched off" in result.content

    async def test_going_from_offered_to_switched_off(self, runtime_factory):
        """The reconciliation runs both ways: the direct server goes away and
        only the hidden one is left — the set re-registers switched off."""
        runtime = runtime_factory(
            {
                "direct": stdio_entry(),
                "hidden": stdio_entry(exposure="hidden"),
            }
        )
        direct = await connect(runtime, "direct", resource_server("direct-res"))
        await connect(runtime, "hidden", resource_server("hidden-res"))
        assert "read_mcp_resource" in runtime._ctx.tools.names(audience="model")

        direct.state = STATE_DISCONNECTED
        runtime._apply_resource_tools()

        assert runtime._ctx.tools.get("read_mcp_resource") is not None
        assert "read_mcp_resource" not in runtime._ctx.tools.names(audience="model")
        assert "read_mcp_resource" not in runtime._ctx.tools.names(audience="program")

    async def test_a_late_direct_server_brings_the_tools_back(self, runtime_factory):
        """Switched off, then a direct server connects — the reconciled set
        re-registers enabled (the disable goes with the old form)."""
        runtime = runtime_factory(
            {"hidden": stdio_entry(exposure="hidden"), "direct": stdio_entry()}
        )
        await connect(runtime, "hidden", resource_server("hidden-res"))
        assert "read_mcp_resource" not in runtime._ctx.tools.names(audience="program")

        await connect(runtime, "direct", resource_server("direct-res"))
        assert runtime._ctx.tools.get("read_mcp_resource").availability == "both"
        assert "read_mcp_resource" in runtime._ctx.tools.names(audience="model")


# ── the codemode warning ─────────────────────────────────────


class TestCodemodeWarning:
    async def test_program_only_resource_tools_join_the_warning(
        self, runtime_factory
    ):
        """The resource server carries no tools of its own — only the
        program-only resource set can raise the warning here."""
        runtime = runtime_factory(
            {"demo": stdio_entry(exposure="codemode")}
        )
        await connect(runtime, "demo", resource_server())
        assert runtime._codemode_warned is True

    async def test_switched_off_resource_tools_do_not(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry(exposure="hidden")})
        await connect(runtime, "demo", resource_server())
        assert runtime._codemode_warned is False

    async def test_model_visible_resource_tools_do_not(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        assert runtime._codemode_warned is False


# ── the server argument (decision D1) ────────────────────────


class TestServerArgument:
    async def test_omitted_with_one_server_is_that_server(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert result.content == "ship it"
        assert result.details["server"] == "demo"  # the configured name

    async def test_omitted_with_several_servers_errors(self, runtime_factory):
        runtime = runtime_factory(
            {"one": stdio_entry(), "two": stdio_entry()}
        )
        await connect(runtime, "one", resource_server("one-res"))
        await connect(runtime, "two", resource_server("two-res"))
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "server" in err.value.message

    async def test_a_name_picks_the_server(self, runtime_factory):
        runtime = runtime_factory(
            {"one": stdio_entry(), "two": stdio_entry()}
        )
        await connect(runtime, "one", resource_server("one-res", note="one's note"))
        await connect(runtime, "two", resource_server("two-res", note="two's note"))
        tool = runtime._ctx.tools.get("read_mcp_resource")

        result = await asyncio.wait_for(
            tool.run_async({"server": "two", "uri": "note://today"}), BOUND
        )
        assert result.content == "two's note"
        assert result.details["server"] == "two"

    async def test_an_unknown_server_errors(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                tool.run_async({"server": "ghost", "uri": "note://today"}), BOUND
            )
        assert err.value.code == "mcp_error"

    async def test_a_dashed_name_matches_folded_or_verbatim(self, runtime_factory):
        """The prompt section shows the configured name (``my-server``) and
        the tool namespace shows the folded one (``mcp__my_server__…``) —
        either spelling picks the server."""
        runtime = runtime_factory({"my-server": stdio_entry()})
        await connect(runtime, "my_server", resource_server("my-server"))
        tool = runtime._ctx.tools.get("read_mcp_resource")

        verbatim = await asyncio.wait_for(
            tool.run_async({"server": "my-server", "uri": "note://today"}), BOUND
        )
        folded = await asyncio.wait_for(
            tool.run_async({"server": "my_server", "uri": "note://today"}), BOUND
        )
        assert verbatim.content == folded.content == "ship it"
        assert folded.details["server"] == "my-server"

    async def test_uri_is_the_only_required_argument(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "missing_param"


# ── reading (decision D4) ────────────────────────────────────


class TestReadResource:
    async def test_text_lands_in_content(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert result.content == "ship it"
        assert result.details["uri"] == "note://today"
        assert result.details["mimeType"] == "text/plain"
        assert result.details["is_error"] is False
        assert "images" not in result.details
        assert "files" not in result.details

    async def test_a_template_uri_reads_through_the_server(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(
            tool.run_async({"uri": "greeting://ada"}), BOUND
        )
        assert result.content == "hello ada"

    async def test_an_image_lands_in_details(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "pic://logo"}), BOUND)
        assert "[image: image/png]" in result.content
        assert result.details["images"] == [
            {
                "type": "image",
                "data": base64.b64encode(PNG).decode(),
                "mimeType": "image/png",
            }
        ]

    async def test_another_blob_is_spooled_to_a_temp_file(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "blob://data"}), BOUND)

        spooled = result.details["files"]
        assert len(spooled) == 1
        entry = spooled[0]
        assert entry["mimeType"] == "application/octet-stream"
        assert entry["size"] == len(BLOB)
        path = Path(entry["path"])
        assert path.exists()
        assert path.read_bytes() == BLOB
        assert f"[file: {entry['path']} ({len(BLOB)} bytes" in result.content

    async def test_a_missing_resource_is_an_mcp_error(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://absent"}), BOUND)
        assert err.value.code == "mcp_error"

    async def test_a_uri_of_another_server_is_an_mcp_error(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                tool.run_async({"uri": "other://server/thing"}), BOUND
            )
        assert err.value.code == "mcp_error"

    async def test_the_legacy_error_code_maps_to_mcp_error(self, runtime_factory):
        runtime = runtime_factory({"legacy": stdio_entry()})
        await connect(runtime, "legacy", legacy_server())
        tool = runtime._ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://gone"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "legacy" in err.value.message


# ── listing (decision D5) ────────────────────────────────────


class TestListing:
    async def test_resources_list_name_uri_and_mime(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("list_mcp_resources")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert "today: note://today [text/plain] — today's note" in result.content
        assert "blob: blob://data [application/octet-stream]" in result.content
        assert [r["uri"] for r in result.details["resources"]] == [
            "note://today",
            "pic://logo",
            "blob://data",
        ]
        assert "nextCursor" not in result.details

    async def test_an_empty_listing_says_so(self, runtime_factory):
        runtime = runtime_factory({"paged": stdio_entry()})
        await connect(runtime, "paged", paged_server())
        tool = runtime._ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert result.content == "no resource templates"
        assert result.details["resourceTemplates"] == []

    async def test_templates_list_their_uri_template(self, runtime_factory):
        runtime = runtime_factory({"withres": stdio_entry()})
        await connect(runtime, "withres", resource_server())
        tool = runtime._ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(tool.run_async({"server": "withres"}), BOUND)
        assert "greeting://{name}" in result.content
        assert [t["uriTemplate"] for t in result.details["resourceTemplates"]] == [
            "greeting://{name}"
        ]

    async def test_the_cursor_passes_through_and_next_cursor_comes_back(
        self, runtime_factory
    ):
        runtime = runtime_factory({"paged": stdio_entry()})
        await connect(runtime, "paged", paged_server())
        tool = runtime._ctx.tools.get("list_mcp_resources")

        first = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert "first: note://first" in first.content
        assert first.details["nextCursor"] == "page-2"

        second = await asyncio.wait_for(
            tool.run_async({"cursor": first.details["nextCursor"]}), BOUND
        )
        assert "second: note://second" in second.content
        assert "nextCursor" not in second.details

    async def test_a_non_string_cursor_is_rejected(self, runtime_factory):
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        tool = runtime._ctx.tools.get("list_mcp_resources")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"cursor": 5}), BOUND)
        assert err.value.code == "invalid_type"


# ── the status report is untouched ───────────────────────────


class TestStatusReport:
    async def test_the_status_report_is_unchanged(self, runtime_factory):
        """What mcp_status renders must not move because resources exist —
        the resource tools are one per conversation, not per server."""
        runtime = runtime_factory({"demo": stdio_entry()})
        await connect(runtime, "demo", resource_server())
        assert runtime.status() == [
            {"name": "demo", "state": "connected", "tools": 0, "error": None}
        ]
