"""McpRuntime — the per-conversation MCP state.

Built once per conversation (in the plugin's ``build()``), the runtime owns
what a conversation knows about its servers: the merged configuration, one
:class:`~mocode.host.plugin.builtin.mcp.session.StdioSession` per enabled
server, and the mapping from server tool names to registered mocode tools.
Servers whose tools may be offered to the model (``direct`` exposure) get a
bounded wait during ``start()`` — the first turn must not wait on a slow
server; everything else connects in the background and registers its tools
when it arrives. A legacy ``tools/list_changed`` notification re-syncs the
tool set. Nothing here lives on the plugin instance — one plugin object
serves every conversation in the process.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .....core.events import Notice
from ...context import BuildContext
from ...loader import report
from .config import McpServerConfig, load_servers
from .naming import (
    assign_tool_names,
    availability_for,
    canon_exposure,
    default_exposure,
    resolve_exposure,
)
from .session import STATE_ERROR, StdioSession
from .tools import mcp_status_tool, mcp_tool

if TYPE_CHECKING:
    from ...context import HostContext


def _positive(value: object, default: float) -> float:
    """A positive number from the plugins.mcp settings, else *default*."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


class McpRuntime:
    """Sessions + registered tools + status for one conversation."""

    def __init__(self, ctx: BuildContext):
        self._ctx = ctx
        mcp_config = ctx.plugin_config("mcp")
        self.config: dict[str, McpServerConfig] = load_servers(
            mcp_config=mcp_config,
            cwd=ctx.cwd,
            home=ctx.home,
            plugin_sources=list(ctx.plugin_sources),
        )
        codemode = ctx.config.plugins.get("codemode", {})
        self.codemode_enabled = (
            isinstance(codemode, dict) and codemode.get("enabled") is True
        )
        self.default_exposure = default_exposure(
            mcp_config, codemode_enabled=self.codemode_enabled
        )
        self.connect_timeout = _positive(mcp_config.get("connect_timeout_s"), 10.0)
        self.request_timeout = _positive(mcp_config.get("request_timeout_s"), 60.0)
        #: folded server key -> StdioSession
        self.sessions: dict[str, StdioSession] = {}
        #: background connect tasks, cancelled on shutdown
        self.tasks: list[asyncio.Task] = []
        #: folded server key -> {full tool name -> raw tool name}
        self._registered: dict[str, dict[str, str]] = {}
        #: folded server key -> {raw tool name -> full tool name} — the
        #: collision-aware assignment, exposed for the tool builders
        self.assignments: dict[str, dict[str, str]] = {}
        self._codemode_warned = False

    # ── lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Connect every enabled server — direct ones with a bounded wait,
        the rest in the background. Connection failures are reported and
        remembered on the session, never raised."""
        for key, cfg in self.config.items():
            if not cfg.enabled:
                continue
            if cfg.timeout is None:
                cfg.timeout = self.request_timeout
            session = StdioSession(
                cfg,
                on_connected=self._on_connected,
                on_tools_changed=self._on_tools_changed,
            )
            self.sessions[key] = session
            if self._must_be_declared(cfg):
                try:
                    await asyncio.wait_for(
                        session.connect_and_register(), timeout=self.connect_timeout
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    message = (
                        f"connection timed out after {self.connect_timeout:g}s"
                    )
                    report(f"mcp: server {cfg.name!r}: {message}")
                    session.last_error = message
                    session.state = STATE_ERROR
                except Exception as e:
                    report(f"mcp: server {cfg.name!r}: connection failed: {e}")
                    session.last_error = str(e) or "connection failed"
                    session.state = STATE_ERROR
            else:
                self.tasks.append(
                    asyncio.create_task(self._connect_in_background(key, session))
                )
        await self._maybe_warn_codemode()

    async def shutdown_background(self) -> None:  # pragma: no cover - convenience
        ...

    def shutdown(self) -> None:
        """Sync teardown for the plugin's ``close()`` — cancel background
        connects and kill every child without awaiting I/O."""
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        for session in self.sessions.values():
            try:
                session.shutdown()
            except Exception:
                pass  # one bad server must not stop the rest from dying

    # ── registration ────────────────────────────────────────

    async def _connect_in_background(self, key: str, session: StdioSession) -> None:
        try:
            await session.connect_and_register()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            cfg = self.config[key]
            report(f"mcp: server {cfg.name!r}: background connection failed: {e}")
            session.last_error = str(e) or "connection failed"
            session.state = STATE_ERROR
        await self._maybe_warn_codemode()

    async def _on_connected(
        self, session: StdioSession, tools: list[dict]
    ) -> None:
        self._apply_tools(session, tools)
        await self._maybe_warn_codemode()

    async def _on_tools_changed(self, session: StdioSession) -> None:
        await self.sync_tools(session)

    async def sync_tools(self, session: StdioSession) -> None:
        """Re-list a server's tools and reconcile the registry (legacy
        ``tools/list_changed``) — newcomers register, the gone unregister."""
        tools = await session.list_tools()
        self._apply_tools(session, tools)

    def _apply_tools(self, session: StdioSession, tools: list[dict]) -> None:
        key = self._key_for(session)
        if key is None:
            return
        cfg = self.config[key]
        raw_names = [
            t["name"]
            for t in tools
            if isinstance(t, dict) and isinstance(t.get("name"), str)
        ]
        assigned = assign_tool_names(key, raw_names)
        self.assignments[key] = assigned
        current = self._registered.setdefault(key, {})
        live = set(assigned.values())
        for full_name in [n for n in current if n not in live]:
            self._ctx.tools.unregister(full_name)
            del current[full_name]
        for full_name, raw_name in {full: raw for raw, full in assigned.items()}.items():
            if full_name in current:
                continue
            raw_tool = next(
                (t for t in tools if isinstance(t, dict) and t.get("name") == raw_name),
                {"name": raw_name},
            )
            exposure = resolve_exposure(cfg, raw_name, self.default_exposure)
            availability, disabled = availability_for(exposure)
            tool = mcp_tool(self, session, cfg.name, raw_tool, availability, disabled)
            self._ctx.tools.register(tool)
            if disabled:
                self._ctx.tools.disable(full_name)
            current[full_name] = raw_name

    def _key_for(self, session: StdioSession) -> str | None:
        for key, candidate in self.sessions.items():
            if candidate is session:
                return key
        return None

    def _must_be_declared(self, cfg: McpServerConfig) -> bool:
        """Whether any tool of this server can be offered to the model — the
        first turn's request must declare it, so its server connects under
        a bounded wait (decision D9)."""
        if cfg.exposure is not None and canon_exposure(cfg.exposure) == "direct":
            return True
        if self.default_exposure == "direct":
            return True
        return any(
            canon_exposure(value) == "direct" for value in cfg.tool_exposure.values()
        )

    # ── status ──────────────────────────────────────────────

    def status(self) -> list[dict]:
        """One entry per configured server: name, state, tool count, error."""
        out = []
        for key, cfg in self.config.items():
            session = self.sessions.get(key)
            if not cfg.enabled:
                out.append(
                    {"name": cfg.name, "state": "disabled", "tools": 0, "error": None}
                )
            elif session is None:
                out.append(
                    {"name": cfg.name, "state": "pending", "tools": 0, "error": None}
                )
            else:
                out.append(
                    {
                        "name": cfg.name,
                        "state": session.state,
                        "tools": len(self._registered.get(key, {})),
                        "error": session.last_error,
                    }
                )
        return out

    # ── the codemode warning ────────────────────────────────

    async def _maybe_warn_codemode(self) -> None:
        """At most once per conversation: program-reachable tools while the
        codemode plugin is off are unreachable — say so."""
        if self._codemode_warned or self.codemode_enabled:
            return
        count = 0
        for key, session in self.sessions.items():
            cfg = self.config[key]
            for raw_name in self._registered.get(key, {}).values():
                exposure = resolve_exposure(cfg, raw_name, self.default_exposure)
                if exposure in ("codemode", "deferred"):
                    count += 1
        if count == 0:
            return
        self._codemode_warned = True
        emit = getattr(self._ctx, "emit", None)
        if emit is None:
            return
        await emit(
            Notice(
                message=(
                    f"{count} MCP tools are reachable only through codemode, "
                    "which is disabled (set plugins.codemode.enabled); run "
                    "mocode with the codemode plugin to use them."
                ),
                level="warn",
            )
        )


__all__ = ["McpRuntime", "mcp_status_tool", "mcp_tool"]
