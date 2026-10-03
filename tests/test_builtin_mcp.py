"""The mcp builtin plugin — configuration, naming, wire, resources, tools and
the plugin lifecycle, against the official SDK's client.

Two seams, one per job. The protocol shape (era negotiation, wire-form
results, error mapping, pagination, the resource methods, the modern
tool-change subscription) is exercised through an **in-process wire peer**:
a scripted JSON-RPC server over the SDK's memory streams
(:class:`WirePeer`), plus the SDK's own in-process ``MCPServer`` /
``InMemorySubscriptionBus`` shapes. Nothing here spawns a child.

The child process itself — spawn, environment, stderr tail, teardown that
reaps it — is the *other* seam and lives in
``tests/test_builtin_mcp_process.py``. The loopback HTTP transports live
here, because what they prove is what the plugin builds out of an entry's
``transport`` / ``url`` / ``headers``.

Every async path is bounded so a broken peer can never hang the suite on
Windows. Waiting on an event that travels through the wire goes through
``tests.conftest.wait_until`` — no hand-rolled polling, no bare sleeps.
"""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
from collections.abc import Iterator
from functools import partial
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from mcp.server import MCPServer
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ToolsListChanged
from mcp.types import (
    ListResourceTemplatesResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

from mocode.core.agent import AgentConfig
from mocode.core.dispatch import ToolDispatcher
from mocode.core.events import Notice, ToolCallFinished, ToolCallStarted
from mocode.core.hook import HookRunner
from mocode.core.tool import ToolError
from mocode.host.config import Config
from mocode.host.plugin.builtin.mcp import PLUGIN, McpPlugin
from mocode.host.plugin.builtin.mcp.client import (
    ERA_LEGACY,
    ERA_MODERN,
    STATE_CLOSED,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_ERROR,
    McpError,
    McpSession,
)
from mocode.host.plugin.builtin.mcp.config import (
    MCP_SCHEMA_1_0_0,
    PLUGIN_DATA_DIR,
    McpServerConfig,
    load_servers,
)
from mocode.host.plugin.builtin.mcp.naming import (
    assign_tool_names,
    availability_for,
    canon_exposure,
    default_exposure,
    fold_server_name,
    normalize,
    resolve_exposure,
    resolve_server_exposure,
    tool_full_name,
)
from mocode.host.plugin.builtin.mcp.runtime import McpRuntime
from mocode.host.plugin.builtin.mcp.subscriptions import BACKOFF_INITIAL, watch_tools
from mocode.host.plugin.builtin.mcp.tools import (
    RESOURCE_TOOL_NAMES,
    mcp_status_tool,
    mcp_tool,
)
from mocode.host.plugin.context import BuildContext

from ._mcp_fake import ByToolName, Drop, Late, Silent, Unsupported, WirePeer
from .conftest import settle, wait_until

BOUND = 15  # seconds — every session operation in this file stays bounded

#: The import-time ``asyncio.sleep``. The autouse guard patches the module
#: attribute, never this name, so a zero-delay yield through it costs no
#: wall clock and is invisible to the guard — the same shape ``settle(0)``
#: stands for elsewhere.
_REAL_ASYNCIO_SLEEP = asyncio.sleep


def write_mcp_json(path: Path, data: dict | str) -> Path:
    """Write one mcp.json — a dict is dumped, a str written verbatim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    return path


def stdio_entry(command: str = "tool", **extra) -> dict:
    return {"type": "stdio", "command": command, **extra}

class TestLoadServers:
    def test_the_files_precedence_and_a_projects_wholesale_replacement(self, tmp_path):
        # nothing configured is an empty table; an inline config.json entry
        # carries its source
        assert load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home") == {}
        merged = load_servers(
            mcp_config={"servers": {"demo": {"command": "run"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["demo"]
        assert merged["demo"].command == "run"
        assert merged["demo"].source == "config.json"

        home = tmp_path / "home"
        write_mcp_json(home / "mcp.json", {"mcpServers": {"s": stdio_entry("home")}})
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": {"s": stdio_entry("proj"), "demo": stdio_entry("low")}},
        )
        plugin_dir = tmp_path / "plugins" / "acme"
        plugin_mcp = {"$schema": MCP_SCHEMA_1_0_0, "mcpServers": {"s": stdio_entry("plug")}}
        write_mcp_json(plugin_dir / "mcp.json", plugin_mcp)

        # the project file is read alongside the home one
        both = load_servers(mcp_config={}, cwd=tmp_path, home=home)
        assert set(both) == {"s", "demo"}

        # a project entry replaces the home entry wholesale — nothing bleeds
        # through from the loser
        write_mcp_json(
            home / "mcp.json",
            {"mcpServers": {"s": stdio_entry("home"), "demo": stdio_entry("home-demo", args=["a"], env={"K": "V"})}},
        )
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=home)
        cfg = merged["demo"]
        assert cfg.command == "low"
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.timeout is None

        # project beats home beats plugin
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=home, plugin_sources=[plugin_dir]
        )
        assert merged["s"].command == "proj"
        # …and inline beats them all
        merged = load_servers(
            mcp_config={"servers": {"s": {"command": "inline"}}},
            cwd=tmp_path,
            home=home,
            plugin_sources=[plugin_dir],
        )
        assert merged["s"].command == "inline"

        # a relative cwd resolves against its own file's directory, and an
        # absolute one is kept as it is
        project = tmp_path / "proj"
        write_mcp_json(
            project / ".mocode" / "mcp.json",
            {"mcpServers": {"demo": stdio_entry(cwd="./data")}},
        )
        write_mcp_json(home / "mcp.json", {"mcpServers": {"other": stdio_entry(cwd="sub")}})
        merged = load_servers(mcp_config={}, cwd=project, home=home)
        assert merged["demo"].cwd == str((project / ".mocode" / "data").resolve())
        assert merged["other"].cwd == str((home / "sub").resolve())
        merged = load_servers(
            mcp_config={"servers": {"demo": stdio_entry(cwd=str(tmp_path))}},
            cwd=project,
            home=tmp_path / "home",
        )
        assert merged["demo"].cwd == str(tmp_path)

    def test_names_differing_only_in_separator_are_one_server(self, tmp_path, capsys):
        home = tmp_path / "home"
        write_mcp_json(home / "mcp.json", {"mcpServers": {"my_srv": stdio_entry("low")}})
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json", {"mcpServers": {"my-srv": stdio_entry("high")}}
        )
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=home)
        assert len(merged) == 1
        assert merged["my_srv"].command == "high"
        assert "lower-priority entry is dropped" in capsys.readouterr().err

    def test_invalid_entries_are_skipped_without_hurting_valid_ones(self, tmp_path, capsys):
        bad: dict = {
            "not-an-object": 42,
            "no-command": {"type": "stdio"},
            "empty-command": stdio_entry("  "),
            "bad-args": stdio_entry(args="nope"),
            "bad-args-item": stdio_entry(args=[1]),
            "bad-env": stdio_entry(env=["K"]),
            "bad-env-value": stdio_entry(env={"K": 1}),
            "bad-cwd": stdio_entry(cwd=3),
            "bad-type": {"type": "websocket", "command": "x"},
            "scheme": {"type": "streamable-http", "url": "ftp://u/mcp"},
            "cleartext": {"type": "streamable-http", "url": "http://example.com/mcp"},
            "userinfo": {"type": "streamable-http", "url": "https://user:pw@example.com/mcp"},
            "fragment": {"type": "streamable-http", "url": "https://u/mcp#frag"},
            "relative": {"type": "streamable-http", "url": "/mcp"},
            "no-url": {"type": "streamable-http"},
            "bad-url": {"type": "streamable-http", "url": 42},
            "bad-headers": {"type": "streamable-http", "url": "https://u/mcp", "headers": {"X": 1}},
            "headers-list": {"type": "streamable-http", "url": "https://u/mcp", "headers": ["X"]},
            "case-clash": {
                "type": "streamable-http",
                "url": "https://u/mcp",
                "headers": {"Authorization": "a", "authorization": "b"},
            },
            "expanded-name": {
                "type": "streamable-http",
                "url": "https://u/mcp",
                "headers": {"X-${MCP_TEST_VAR}": "v"},
            },
            "sse-cleartext": {"type": "sse", "url": "http://example.com/sse"},
            "good": stdio_entry(),
        }
        merged = load_servers(mcp_config={"servers": bad}, cwd=tmp_path, home=tmp_path / "home")
        assert list(merged) == ["good"]
        err = capsys.readouterr().err
        for name in bad:
            if name != "good":
                assert name in err

        # and the transports the loader accepts — sse, streamable-http
        # (explicit, inferred and aliased) and a loopback that may stay http
        merged = load_servers(
            mcp_config={
                "servers": {
                    "old": {"type": "sse", "url": "https://x/sse"},
                    "web": {"type": "streamable-http", "url": "http://localhost/mcp"},
                    "web2": {"url": "http://localhost/mcp"},
                    "alias": {"type": "http", "url": "https://u/mcp"},
                    "v4": {"type": "sse", "url": "http://127.0.0.1:9000/sse"},
                    "v6": {"type": "sse", "url": "http://[::1]:9000/sse"},
                    "name": {"type": "sse", "url": "http://localhost/sse"},
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["old", "web", "web2", "alias", "v4", "v6", "name"]
        # the type is optional in mocode files and inferred from the entry
        assert merged["old"].transport == "sse"
        assert merged["web"].transport == "streamable-http"
        assert merged["web2"].transport == "streamable-http"  # inferred, no type
        assert merged["alias"].transport == "streamable-http"  # http is the alias
        assert merged["v4"].transport == "sse"  # a loopback may stay http
        assert merged["v6"].url == "http://[::1]:9000/sse"

    def test_variables_and_placeholders(
        self, tmp_path, capsys
    ):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "web": {
                        "type": "streamable-http",
                        "url": "https://${MCP_TEST_HOST}/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                    },
                    "demo": stdio_entry(
                        env={"TOKEN": "${MCP_TEST_TOKEN}"}, args=["${MCP_TEST_ARG}"]
                    ),
                    "empty-header": {
                        "url": "https://h/mcp",
                        "headers": {"X-Token": "${MCP_TEST_MISSING}"},
                    },
                    "empty-env": stdio_entry(env={"TOKEN": "${MCP_MISSING_VAR}"}),
                    "bang": stdio_entry(env={"K": "!echo hi"}),
                },
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={
                "MCP_TEST_HOST": "mcp.example",
                "MCP_TEST_TOKEN": "secret",
                "MCP_TEST_ARG": "value",
            },
        )
        assert list(merged) == ["web", "demo", "empty_header", "empty_env", "bang"]
        cfg = merged["web"]
        assert cfg.url == "https://mcp.example/mcp"
        assert cfg.headers == {"Authorization": "Bearer secret"}
        cfg = merged["demo"]
        assert cfg.env == {"TOKEN": "secret"}
        assert cfg.args == ["value"]
        assert merged["empty_header"].headers == {"X-Token": ""}
        assert merged["empty_env"].env == {"TOKEN": ""}
        # a bang command is reported and kept literal — never evaluated
        assert merged["bang"].env == {"K": "!echo hi"}
        err = capsys.readouterr().err
        for word in ("MCP_TEST_MISSING", "MCP_MISSING_VAR", "'!command'"):
            assert word in err

        # plugin tokens mean nothing in a mocode file — an entry that uses
        # one is skipped, not half-expanded
        merged = load_servers(
            mcp_config={
                "servers": {
                    "arg": stdio_entry(args=["${PLUGIN_ROOT}/x"]),
                    "env": stdio_entry(env={"K": "${PLUGIN_DATA}"}),
                    "cwd": stdio_entry(cwd="${PLUGIN_ROOT}"),
                    "good": stdio_entry(),
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={},
        )
        assert list(merged) == ["good"]
        assert "only mean something inside a plugin" in capsys.readouterr().err

    def test_the_extensions_are_kept_and_bad_values_fall_back(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "http_web": {
                        "type": "streamable-http",
                        "url": "https://u/mcp",
                        "enabled": False,
                        "timeout": 12,
                        "exposure": "hidden",
                        "description": "one line",
                    },
                    "demo": {
                        "command": "x",
                        "enabled": False,
                        "timeout": 12,
                        "exposure": "hidden",
                        "toolExposure": {"a": "direct"},
                        "description": "one line",
                    },
                    "bad": {
                        "command": "x",
                        "enabled": "yes",
                        "timeout": "later",
                        "exposure": 3,
                        "toolExposure": ["a"],
                        "description": {},
                    },
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        for name in ("http_web", "demo"):
            cfg = merged[name]
            assert cfg.enabled is False
            assert cfg.timeout == 12.0
            assert cfg.exposure == "hidden"
            assert cfg.description == "one line"
        assert merged["demo"].tool_exposure == {"a": "direct"}
        cfg = merged["bad"]
        assert cfg.enabled is True
        assert cfg.timeout is None
        assert cfg.exposure is None
        assert cfg.tool_exposure == {}
        assert cfg.description == ""
        err = capsys.readouterr().err
        for word in ("'enabled'", "'timeout'", "'exposure'", "'toolExposure'", "'description'"):
            assert word in err

    def test_a_broken_file_or_a_shape_wrong_table_is_reported(self, tmp_path, capsys):
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", "{not json")
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home")
        assert merged == {}
        assert "unreadable mcp.json" in capsys.readouterr().err

        write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"mcpServers": ["a"]})
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home")
        assert merged == {}
        assert "must be an object" in capsys.readouterr().err


class TestPluginFileRules:
    def _plugin_mcp(self, servers: dict, **top) -> dict:
        return {"$schema": MCP_SCHEMA_1_0_0, "mcpServers": servers, **top}

    def test_a_plugin_file_is_all_or_nothing(self, tmp_path, capsys):
        # a valid file loads, defaults its cwd to the plugin root, and gets
        # its data directory created
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": stdio_entry()}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        cfg = merged["srv"]
        assert cfg.command == "tool"
        assert cfg.cwd == str(plugin_dir.resolve())  # omitted cwd defaults to the root
        assert cfg.plugin_root == plugin_dir
        assert cfg.plugin_data == plugin_dir / PLUGIN_DATA_DIR
        assert (plugin_dir / PLUGIN_DATA_DIR).is_dir()

        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(plugin_dir / "mcp.json", {"mcpServers": {"srv": stdio_entry()}})
        other_dir = tmp_path / "plugins" / "other"
        write_mcp_json(
            other_dir / "mcp.json",
            {
                "$schema": "https://agent-plugins.org/schemas/9.9.9/mcp.schema.json",
                "mcpServers": {"srv": stdio_entry()},
            },
        )
        extra_dir = tmp_path / "plugins" / "extra"
        write_mcp_json(extra_dir / "mcp.json", self._plugin_mcp({"srv": stdio_entry()}, extra=1))
        merged = load_servers(
            mcp_config={},
            cwd=tmp_path,
            home=tmp_path / "home",
            plugin_sources=[plugin_dir, other_dir, extra_dir],
        )
        # a missing schema, a mismatched one and a stray top-level field each
        # take the whole file down — entries are never half-loaded
        assert merged == {}
        err = capsys.readouterr().err
        assert err.count("MCP configuration is skipped") == 3
        assert "unknown top-level field" in err

        # the type every entry must carry
        type_dir = tmp_path / "plugins" / "typed"
        write_mcp_json(type_dir / "mcp.json", self._plugin_mcp({"srv": {"command": "x"}}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[type_dir]
        )
        assert merged == {}
        assert "missing 'type'" in capsys.readouterr().err

    def test_plugin_placeholders_expand_once(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        entry = stdio_entry(
            command="./bin/tool",
            args=["${PLUGIN_ROOT}/cfg.json", "${PLUGIN_DATA}"],
            env={"CONF": "${PLUGIN_ROOT}/conf"},
            cwd="${PLUGIN_DATA}/run",
        )
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": entry}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        cfg = merged["srv"]
        root, data = str(plugin_dir), str(plugin_dir / PLUGIN_DATA_DIR)
        assert cfg.command == "./bin/tool"  # command itself is never expanded
        assert cfg.args == [f"{root}/cfg.json", data]
        assert cfg.env == {"CONF": f"{root}/conf"}
        assert cfg.cwd == str((Path(data) / "run").resolve())

        # the mocode-only extensions are reported and ignored here
        ext = stdio_entry(exposure="hidden", timeout=5, description="nope")
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": ext}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        cfg = merged["srv"]
        assert cfg.exposure is None
        assert cfg.timeout is None
        assert cfg.description == ""
        assert "unknown field(s)" in capsys.readouterr().err

    def test_the_plugin_placeholders_are_read_only(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp(
                {
                    "escapes-root": stdio_entry(cwd="${PLUGIN_ROOT}/../out"),
                    "escapes-data": stdio_entry(cwd="${PLUGIN_DATA}/../../out"),
                    "abs-outside": stdio_entry(cwd="/tmp"),
                    "bad-shape": stdio_entry(cwd="sub/dir"),
                    "plain-dot": stdio_entry(cwd="./sub"),
                }
            ),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert list(merged) == ["plain_dot"]
        assert merged["plain_dot"].cwd == str((plugin_dir / "sub").resolve())
        err = capsys.readouterr().err
        assert "escapes" in err
        assert "must start with './'" in err

        # and an entry may not define the placeholders itself
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp({"bad": stdio_entry(env={"PLUGIN_ROOT": "/x"})}),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert merged == {}
        assert "must not set 'PLUGIN_ROOT'" in capsys.readouterr().err

    def test_http_entries_in_plugin_files_validate_and_expand_nothing(
        self, tmp_path, capsys
    ):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp(
                {
                    "web": {
                        "type": "streamable-http",
                        "url": "http://127.0.0.1:8080/mcp",
                        "headers": {"Authorization": "Bearer t"},
                    },
                    "feed": {"type": "sse", "url": "https://u/sse"},
                    "var-url": {"type": "streamable-http", "url": "https://${MCP_TEST_HOST}/mcp"},
                    "bang-url": {"type": "streamable-http", "url": "!echo hi"},
                    "var-value": {
                        "type": "streamable-http",
                        "url": "https://u/mcp",
                        "headers": {"X": "${MCP_TEST_TOKEN}"},
                    },
                    "var-name": {
                        "type": "streamable-http",
                        "url": "https://u/mcp",
                        "headers": {"X-${MCP_TEST_VAR}": "v"},
                    },
                    "plugin-token": {"type": "streamable-http", "url": "https://${PLUGIN_ROOT}/mcp"},
                    "good": {"type": "streamable-http", "url": "https://u/mcp"},
                }
            ),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert list(merged) == ["web", "feed", "good"]
        assert merged["web"].transport == "streamable-http"
        assert merged["web"].url == "http://127.0.0.1:8080/mcp"
        assert merged["web"].headers == {"Authorization": "Bearer t"}
        assert merged["feed"].transport == "sse"
        assert merged["feed"].url == "https://u/sse"
        # nothing in a plugin file expands — a placeholder spelling is an
        # error, not a hint
        err = capsys.readouterr().err
        assert err.count("entry skipped") == 5
        assert "${VAR}" in err
def _cfg(**kwargs) -> McpServerConfig:
    kwargs.setdefault("name", "demo")
    kwargs.setdefault("command", "x")
    kwargs.setdefault("source", "test")
    return McpServerConfig(**kwargs)


class TestNaming:
    def test_names_fold_and_collisions_get_a_stable_suffix(self):
        import hashlib

        assert normalize("dev-radius") == "dev_radius"
        assert normalize("a b.c/d:e") == "a_b_c_d_e"
        assert normalize("already_ok") == "already_ok"
        assert normalize("UPPER-1") == "UPPER_1"
        assert fold_server_name("my-server") == fold_server_name("my_server")
        assert fold_server_name("a.b") == fold_server_name("a-b")
        assert fold_server_name("ab") != fold_server_name("a-b")
        assert tool_full_name("dev-radius", "search") == "mcp__dev_radius__search"
        assert tool_full_name("srv", "do-thing") == "mcp__srv__do_thing"

        assert assign_tool_names("srv", ["search", "get_one"]) == {
            "search": "mcp__srv__search",
            "get_one": "mcp__srv__get_one",
        }
        # sorted: "a b" < "a-b" < "a_b" — the first keeps the plain name, and
        # the suffix is the raw name's own hash, so the assignment never
        # depends on the order the server listed them in
        out = assign_tool_names("srv", ["a-b", "a_b", "a b"])
        assert set(out) == {"a-b", "a_b", "a b"}
        assert out["a b"] == "mcp__srv__a_b"
        assert out["a-b"] == "mcp__srv__a_b_" + hashlib.sha1(b"a-b").hexdigest()[:6]
        assert out["a_b"] == "mcp__srv__a_b_" + hashlib.sha1(b"a_b").hexdigest()[:6]
        assert assign_tool_names("srv", ["a-b", "a_b", "a b"]) == assign_tool_names(
            "srv", ["a b", "a_b", "a-b"]
        )


class TestExposureRules:
    def test_the_default_exposure_and_the_availability_mapping(self, capsys):
        assert default_exposure({}, codemode_enabled=False) == "direct"
        assert default_exposure({}, codemode_enabled=True) == "codemode"
        assert default_exposure({"default_exposure": "auto"}, codemode_enabled=True) == "codemode"
        assert default_exposure({"default_exposure": "hidden"}, codemode_enabled=True) == "hidden"
        assert (
            default_exposure({"default_exposure": "codemode-deferred"}, codemode_enabled=False)
            == "codemode-deferred"
        )
        assert default_exposure([], codemode_enabled=False) == "direct"  # not a dict
        # garbage reports and falls back to auto
        assert default_exposure({"default_exposure": 7}, codemode_enabled=False) == "direct"
        assert default_exposure({"default_exposure": "bogus"}, codemode_enabled=True) == "codemode"
        assert "default_exposure" in capsys.readouterr().err

        assert availability_for("direct") == ("both", False)
        assert availability_for("codemode") == ("program", False)
        assert availability_for("deferred") == ("program", False)
        assert availability_for("hidden") == ("both", True)

        # the server-level answer, resolved against a default
        assert resolve_server_exposure(_cfg(exposure="hidden"), "direct") == "hidden"
        assert resolve_server_exposure(_cfg(exposure="codemode-deferred"), "direct") == "codemode"
        assert resolve_server_exposure(_cfg(exposure="bogus"), "codemode") == "codemode"
        assert resolve_server_exposure(_cfg(), "deferred") == "deferred"
        assert resolve_server_exposure(_cfg(), "garbage") == "direct"

    def test_the_resolution_order_and_the_pattern_matches(self):
        # exact tool name beats pattern beats server beats default
        cfg = _cfg(exposure="hidden", tool_exposure={"search": "direct", "get_*": "codemode"})
        assert resolve_exposure(cfg, "search", "direct") == "direct"
        assert resolve_exposure(cfg, "get_one", "codemode") == "codemode"
        assert resolve_exposure(cfg, "delete_one", "hidden") == "hidden"
        assert resolve_exposure(_cfg(exposure="hidden"), "anything", "direct") == "hidden"
        assert resolve_exposure(_cfg(), "anything", "codemode") == "codemode"
        assert canon_exposure("codemode-deferred") == "codemode"
        assert resolve_exposure(_cfg(exposure="codemode-deferred"), "t", "direct") == "codemode"

        # a star matches any characters, and the first matching pattern wins
        cfg = _cfg(tool_exposure={"*_x": "direct", "get_*": "hidden"})
        assert resolve_exposure(cfg, "get_x", "codemode") == "direct"
        cfg = _cfg(tool_exposure={"a*c": "direct"})
        assert resolve_exposure(cfg, "aanythingc", "codemode") == "direct"
        assert resolve_exposure(cfg, "aanythingcX", "codemode") == "codemode"

    def test_unknown_values_report_and_fall_through(self, capsys):
        cfg = _cfg(exposure="sideways", tool_exposure={"t": "also-bogus"})
        assert resolve_exposure(cfg, "t", "direct") == "direct"
        cfg = _cfg(tool_exposure={"other": "bogus"})
        assert resolve_exposure(cfg, "other", "codemode") == "codemode"
        assert resolve_exposure(_cfg(), "t", "zzz") == "direct"
        err = capsys.readouterr().err
        assert "sideways" in err and "bogus" in err and "zzz" in err


#: The handshake-era answer for a peer that answers only ``initialize``.
LEGACY_HANDSHAKE = {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "wire-legacy-srv", "version": "1.0"},
    "instructions": "Legacy wire instructions.",
}


def _call_answer(name: str) -> dict:
    """What a modern peer's ``tools/call`` answers for tool *name*."""
    if name == "fail":
        return {
            "resultType": "complete",
            "content": [{"type": "text", "text": "boom"}],
            "isError": True,
        }
    if name == "ask":
        return {
            "resultType": "input_required",
            "inputRequests": {
                "login": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": "log in",
                        "requestedSchema": {"type": "object", "properties": {}},
                    },
                }
            },
            "requestState": "opaque",
        }
    if name == "pic":
        return {
            "resultType": "complete",
            "content": [
                {"type": "text", "text": "here:"},
                {"type": "image", "data": "QUJD", "mimeType": "image/png"},
            ],
            "structuredContent": {"n": 1},
        }
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": "hello"}],
        "structuredContent": {"ok": True},
    }

#: A modern-era peer: discover, tools/list and the shapes the tool mapping
#: reads back (a failing ``fail``, an input-requiring ``ask``, a non-text
#: ``pic``). Its ``tools/call`` answers by the tool the request names —
#: :func:`modern_peer` builds the whole table.
MODERN_PEER = {
    "server/discover": {
        "resultType": "complete",
        "supportedVersions": ["2026-07-28"],
        "capabilities": {"tools": {}},
        "ttlMs": 0,
        "cacheScope": "public",
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {"name": "wire-srv", "version": "1.0"}
        },
        "instructions": "Wire server instructions.",
    },
    "tools/list": {
        "resultType": "complete",
        "tools": [
            {
                "name": "search",
                "description": "Search things",
                "inputSchema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
            {
                "name": "fail",
                "description": "Always fails",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "ask",
                "description": "Needs user input",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "pic",
                "description": "Returns an image",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ],
        "ttlMs": 0,
        "cacheScope": "public",
    },
    "tools/call": ByToolName(
        {
            "search": _call_answer("search"),
            "fail": _call_answer("fail"),
            "ask": _call_answer("ask"),
            "pic": _call_answer("pic"),
        }
    ),
}


#: The same peer without a ``server/discover`` route: the unlisted method
#: answers ``-32601``, which is what makes the client fall back to the
#: handshake and settle on the legacy era.
LEGACY_PEER = {
    key: value for key, value in MODERN_PEER.items() if key != "server/discover"
}


def peer_config(name: str = "demo", **kwargs) -> McpServerConfig:
    """The bookkeeping an in-proc peer session needs — the peer overrides
    the target, so only the identity and the request budget matter."""
    return McpServerConfig(name=name, source="test", **kwargs)


@pytest_asyncio.fixture
async def session_factory():
    """Build McpSessions — over a wire peer (protocol shape) or an in-process
    ``MCPServer``; close them all on teardown."""
    created: list[McpSession] = []

    def factory(
        peer: object = None, *, name: str = "demo", timeout: float = 30.0, **kwargs
    ) -> McpSession:
        session = McpSession(peer_config(name, timeout=timeout), server=peer, **kwargs)
        created.append(session)
        return session

    yield factory
    for session in created:
        try:
            await asyncio.wait_for(session.close(), BOUND)
        except Exception:
            session.shutdown()


class TestSessionWire:
    """The wire shape over the SDK's memory streams: the facts a connect
    negotiates, the wire-form results and blocks, the errors the session
    maps onto its own codes."""

    async def test_a_modern_session_connects_lists_and_calls(self, session_factory):
        connected: list[list[dict]] = []

        async def on_connected(session, tools):
            connected.append(tools)

        session = session_factory(WirePeer(MODERN_PEER), on_connected=on_connected)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "wire-srv", "version": "1.0"}
        assert session.instructions == "Wire server instructions."
        assert session.state == STATE_CONNECTED
        assert session.last_error is None
        assert session.server_capabilities is not None
        assert session.server_capabilities.tools is not None

        tools = session.tools
        assert [t["name"] for t in tools] == ["search", "fail", "ask", "pic"]
        assert len(connected) == 1 and connected[0] == tools
        by_name = {t["name"]: t for t in tools}
        assert by_name["search"]["description"] == "Search things"
        schema = by_name["search"]["inputSchema"]
        assert schema["type"] == "object"
        assert "q" in schema["properties"]  # a JSON Schema object node
        assert schema["required"] == ["q"]

        result = await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "hello"}]
        assert result["structuredContent"] == {"ok": True}

        # a non-text content block passes through beside its structured data
        session = session_factory(
            WirePeer({**MODERN_PEER, "tools/call": _call_answer("pic")}), name="pic"
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("pic"), BOUND)
        block = result["content"][1]
        assert block["type"] == "image"
        assert block["mimeType"] == "image/png"
        assert block["data"] == "QUJD"
        assert result["structuredContent"] == {"n": 1}

    async def test_the_failures_map_to_their_own_errors(self, session_factory):
        # a slow answer costs the call its budget, not the suite its time
        routes = dict(MODERN_PEER)
        routes["tools/call"] = Late(0.3, _call_answer("search"))
        session = session_factory(WirePeer(routes), name="slow", timeout=0.25)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert err.value.code == "mcp_timeout"
        assert session.last_error and "timed out" in session.last_error

        # an input-requiring answer has its own code — no elicitation UI here
        session = session_factory(
            WirePeer({**MODERN_PEER, "tools/call": _call_answer("ask")}), name="ask"
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("ask"), BOUND)
        assert err.value.code == "mcp_input_required"
        assert "elicitation is not supported" in str(err.value)

        # a peer that dies mid-request fails the call instead of hanging it
        routes = dict(MODERN_PEER)
        routes["tools/call"] = Drop()
        session = session_factory(WirePeer(routes), name="drop")
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        # a request in flight when the peer dies must fail, not hang
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert err.value.code == "mcp_transport"
        assert "disconnected" in str(err.value)
        assert session.state == STATE_DISCONNECTED

        # and a closed session refuses the next call
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.close(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert err.value.code == "mcp_closed"

    async def test_the_era_negotiation(self, session_factory):
        # a modern server settles on the current version
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"

        # a server that does not answer the modern probe is legacy — the
        # standard forbids deciding the era on a single error code, and the
        # handshake it falls back to speaks the legacy wire
        session = session_factory(
            WirePeer(
                {
                    **MODERN_PEER,
                    "server/discover": Unsupported(["2025-11-25"]),
                    "initialize": LEGACY_HANDSHAKE,
                }
            ),
            name="neg1",
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "wire-legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy wire instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["search", "fail", "ask", "pic"]

        # a server sharing no version at all fails the connection
        session = session_factory(
            WirePeer({**MODERN_PEER, "server/discover": Unsupported(["2026-08-30"])}),
            name="neg3",
        )
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_error"
        assert session.state == STATE_ERROR
        assert session.last_error

        # and a peer that never answers at all is bounded by the caller
        session = session_factory(
            WirePeer({"server/discover": Silent(), "initialize": Silent()}), name="neg4"
        )
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(session.connect_and_register(), 0.2)
        assert session.state != STATE_CONNECTED


# ── the resource seam ────────────────────────────────────────


import base64

#: A 1x1 PNG — the image resource's bytes.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
#: Raw bytes no model should ever read as text — the blob resource's bytes.
BLOB = b"\x00\x01\x02\x03binary-payload"


def make_resource_server(name: str = "withres", note: str = "ship it") -> MCPServer:
    """An in-process server with one resource of each of the three kinds —
    text, image (``image/*``) and an opaque binary — plus a uri template.
    *note* is the text resource's content, so two servers on one runtime
    stay apart."""
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


async def _list_tools_empty(ctx: Any, params: Any) -> ListToolsResult:
    return ListToolsResult(tools=[])


async def _list_resources_paged(ctx: Any, params: Any) -> ListResourcesResult:
    """Two pages of resources: the first hands out a ``nextCursor``."""
    cursor = getattr(params, "cursor", None)
    if cursor == "page-2":
        return ListResourcesResult(resources=[{"uri": "note://second", "name": "second"}])
    return ListResourcesResult(
        resources=[{"uri": "note://first", "name": "first"}],
        next_cursor="page-2",
    )


async def _list_templates_none(ctx: Any, params: Any) -> ListResourceTemplatesResult:
    return ListResourceTemplatesResult(resource_templates=[])


async def _read_legacy_missing(ctx: Any, params: Any) -> ReadResourceResult:
    """The pre-2026-07-28 answer for an unknown resource: ``-32002``."""
    raise MCPError(code=-32002, message="resource not found (legacy)")


def paged_server() -> LowLevelServer:
    """A low-level server whose resource listing paginates — the cursor
    pass-through shape."""
    return LowLevelServer(
        "paged",
        on_list_tools=_list_tools_empty,
        on_list_resources=_list_resources_paged,
        on_list_resource_templates=_list_templates_none,
    )


def legacy_server() -> LowLevelServer:
    """A low-level server that answers a read with the old error code — the
    protocol dialect says ``-32002`` where a modern one would say something
    else."""
    return LowLevelServer(
        "legacy",
        on_list_tools=_list_tools_empty,
        on_list_resources=_list_resources_paged,
        on_list_resource_templates=_list_templates_none,
        on_read_resource=_read_legacy_missing,
    )


# ── the runtime seam: an in-process server per configured key ──


def inproc_entry(script: str | None = None, **extra) -> dict:
    """A stdio entry the config loader accepts — the in-process server that
    replaces it is never launched."""
    return {"command": "python", "args": ["-c", script or "pass"], **extra}


def make_runtime(
    tmp_path: Path, servers: dict, **extra: Any
) -> tuple[McpRuntime, BuildContext]:
    """A runtime over *servers* plus the context it was built on — every
    entry is a placeholder the test replaces with an in-process server
    through :func:`connect`. ``codemode_enabled`` is a sibling plugin, so
    the codemode plugin has to be switched on for it to be seen."""
    codemode_enabled = extra.pop("codemode_enabled", False)
    plugins: dict[str, Any] = {"mcp": {"servers": servers, **extra}}
    if codemode_enabled:
        plugins["codemode"] = {"enabled": True}
    config = Config(provider="p", model="m", plugins=plugins)
    ctx = BuildContext(home=tmp_path / "home", cwd=tmp_path, config=config)
    return McpRuntime(ctx), ctx


async def connect(runtime: McpRuntime, key: str, server: object) -> McpSession:
    """Wire an in-process *server* into the runtime's slot for *key* and run
    the connect path the plugin runs.

    The two callbacks are the runtime's own: ``McpRuntime.start()`` passes
    exactly these to the sessions it builds, so re-using them here means the
    registration under test is the one the plugin performs — a test asserts
    on what the registry ends up holding, never on the callbacks themselves.
    """
    session = McpSession(
        runtime.config[key],
        server=server,
        on_connected=runtime._on_connected,
        on_tools_changed=runtime._on_tools_changed,
    )
    runtime.sessions[key] = session
    await asyncio.wait_for(session.connect_and_register(), BOUND)
    return session


async def peer_connect(runtime: McpRuntime, key: str, routes: dict) -> McpSession:
    """The same wiring with a scripted wire peer instead of an SDK server —
    what a test uses when it needs a specific wire answer."""
    return await connect(runtime, key, WirePeer(routes))


@pytest_asyncio.fixture
async def runtime_factory(tmp_path):
    """Build runtimes whose sessions are in-process servers; shut every
    session down on teardown."""
    created: list[tuple[McpRuntime, BuildContext]] = []

    def factory(servers: dict, **extra) -> tuple[McpRuntime, BuildContext]:
        pair = make_runtime(tmp_path, servers, **extra)
        created.append(pair)
        return pair

    yield factory
    for runtime, _ctx in created:
        for session in runtime.sessions.values():
            try:
                await asyncio.wait_for(session.close(), BOUND)
            except Exception:
                session.shutdown()
        runtime.shutdown()


def _dispatcher(registry) -> ToolDispatcher:
    """The one execution path, bare — no hooks, events recorded nowhere."""

    async def publish(event, *, fold: bool) -> None:
        return None

    return ToolDispatcher(registry, HookRunner([]), AgentConfig(), publish)


class TestResourceTools:
    """The three read-only tools a resources-capable server lends the whole
    conversation — how they register as that set of servers changes, and how
    the exposure of those servers shows up on them.

    The wire-level pass-throughs (``list_resources``, ``read_resource``) ride
    the same connect path, so they are asserted on the session this test
    already holds.
    """

    async def test_the_tools_register_reconcile_and_follow_the_servers(
        self, runtime_factory, session_factory
    ):
        # the session's own pass-throughs, wire-form — the tools below only
        # split the contents and map the errors
        session = session_factory(make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.server_capabilities.resources is not None
        resources = await asyncio.wait_for(session.list_resources(), BOUND)
        entries = resources["resources"]
        assert [r["uri"] for r in entries] == ["note://today", "pic://logo", "blob://data"]
        assert entries[0]["name"] == "today"
        assert entries[0]["mimeType"] == "text/plain"
        templates = await asyncio.wait_for(session.list_resource_templates(), BOUND)
        assert [t["uriTemplate"] for t in templates["resourceTemplates"]] == [
            "greeting://{name}"
        ]
        result = await asyncio.wait_for(
            session.read_resource("greeting://ada"), BOUND
        )
        assert result["contents"] == [
            {"uri": "greeting://ada", "mimeType": "text/plain", "text": "hello ada"}
        ]

        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())

        registry = ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert name in registry, name
        read = registry.get("read_mcp_resource")
        listing = registry.get("list_mcp_resources")
        templates_tool = registry.get("list_mcp_resource_templates")
        assert read.availability == "both"  # auto exposure → direct (no codemode)
        assert listing.availability == "both"
        assert templates_tool.availability == "both"
        assert read.tags == frozenset({"mcp"})
        assert read.schema["required"] == ["uri"]
        assert "server" in read.schema["properties"]
        assert set(listing.schema["properties"]) == {"server", "cursor"}
        assert listing.schema.get("required") is None
        # what mcp_status renders must not move because resources exist — the
        # resource tools are one per conversation, not per server
        assert runtime.status() == [
            {"name": "demo", "state": "connected", "tools": 0, "error": None}
        ]

        # a server without the capability registers nothing — neither the
        # resource tools nor any per-server tool
        runtime, ctx = runtime_factory({"plain": inproc_entry()})
        session = await connect(
            runtime,
            "plain",
            LowLevelServer("plain-low", on_list_tools=_list_tools_empty),
        )
        assert session.server_capabilities is not None
        assert session.server_capabilities.resources is None
        for name in RESOURCE_TOOL_NAMES:
            assert name not in ctx.tools
        assert [n for n in ctx.tools.names() if n.startswith("mcp__")] == []

        # the reconciliation is idempotent — a repeated connect that changed
        # nothing re-registers nothing, so the objects a caller already holds
        # stay valid
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        session = await connect(runtime, "demo", make_resource_server())
        first = ctx.tools.get("read_mcp_resource")
        await asyncio.wait_for(runtime.sync_tools(session), BOUND)
        assert ctx.tools.get("read_mcp_resource") is first

        # the only resource-capable server goes away and the three tools go
        # with it; a late one brings them back
        session.state = STATE_DISCONNECTED
        await asyncio.wait_for(runtime.sync_tools(session), BOUND)
        for name in RESOURCE_TOOL_NAMES:
            assert name not in ctx.tools
        await connect(runtime, "demo", make_resource_server())
        assert "read_mcp_resource" in ctx.tools

    async def test_the_widest_exposure_of_the_servers_wins(self, runtime_factory):
        # no codemode plugin → auto resolves to direct and both audiences see it
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "both"

        # the codemode plugin switched on → program only
        runtime, ctx = runtime_factory({"demo": inproc_entry()}, codemode_enabled=True)
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "program"

        # an explicit server exposure is honoured
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="codemode")})
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "program"

        # and the widest of the connected servers wins: the program-only one
        # first, then a direct one connects and the tools are offered again
        runtime, ctx = runtime_factory(
            {"readonly": inproc_entry(exposure="codemode"), "direct": inproc_entry()}
        )
        await connect(runtime, "readonly", make_resource_server("readonly"))
        assert ctx.tools.get("read_mcp_resource").availability == "program"
        await connect(runtime, "direct", make_resource_server("direct"))
        assert ctx.tools.get("read_mcp_resource").availability == "both"

    async def test_a_hidden_server_switches_the_tools_off(self, runtime_factory):
        """A hidden server's resources stay invisible to both audiences —
        the same availability_for pipeline a hidden server tool takes."""
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="hidden")})
        await connect(runtime, "demo", make_resource_server())

        registry = ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert registry.get(name) is not None  # registered, like a hidden tool
            assert name not in registry.names(audience="model")
            assert name not in registry.names(audience="program")

        # the dispatcher is the one execution path: a disabled tool's run is
        # refused there, whatever the Tool object itself would do
        result = await asyncio.wait_for(
            _dispatcher(ctx.tools).run(
                "read_mcp_resource", {"uri": "note://today"}, origin="program"
            ),
            BOUND,
        )
        assert result.status == "denied"
        assert "switched off" in result.content

        # the reconciliation runs both ways: the direct server goes away and
        # only the hidden one is left — the set re-registers switched off
        runtime, ctx = runtime_factory(
            {"direct": inproc_entry(), "hidden": inproc_entry(exposure="hidden")}
        )
        direct = await connect(runtime, "direct", make_resource_server("direct-res"))
        await connect(runtime, "hidden", make_resource_server("hidden-res"))
        assert "read_mcp_resource" in ctx.tools.names(audience="model")

        direct.state = STATE_DISCONNECTED
        await asyncio.wait_for(runtime.sync_tools(direct), BOUND)

        assert ctx.tools.get("read_mcp_resource") is not None
        assert "read_mcp_resource" not in ctx.tools.names(audience="model")
        assert "read_mcp_resource" not in ctx.tools.names(audience="program")

        # switched off, then a direct server connects — the reconciled set
        # re-registers enabled (the disable goes with the old form)
        runtime, ctx = runtime_factory(
            {"hidden": inproc_entry(exposure="hidden"), "direct": inproc_entry()}
        )
        await connect(runtime, "hidden", make_resource_server("hidden-res"))
        assert "read_mcp_resource" not in ctx.tools.names(audience="program")
        await connect(runtime, "direct", make_resource_server("direct-res"))
        assert ctx.tools.get("read_mcp_resource").availability == "both"
        assert "read_mcp_resource" in ctx.tools.names(audience="model")


class TestReadingAndListing:
    """The tools' own call path: which server a read goes to, where each
    kind of content lands, and how the listing pages."""

    async def test_the_server_argument_resolves_either_spelling(
        self, runtime_factory
    ):
        # the prompt section shows the configured name (my-server) and the
        # tool namespace the folded one (mcp__my_server__…) — either spelling
        # picks the server
        runtime, ctx = runtime_factory({"my-server": inproc_entry()})
        await connect(runtime, "my_server", make_resource_server("my-server"))
        tool = ctx.tools.get("read_mcp_resource")

        verbatim = await asyncio.wait_for(
            tool.run_async({"server": "my-server", "uri": "note://today"}), BOUND
        )
        folded = await asyncio.wait_for(
            tool.run_async({"server": "my_server", "uri": "note://today"}), BOUND
        )
        assert verbatim.content == folded.content == "ship it"
        assert folded.details["server"] == "my-server"  # the configured name

        # omitted with one server is that server
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(
            tool.run_async({"uri": "note://today"}), BOUND
        )
        assert result.content == "ship it"
        assert result.details["server"] == "demo"

        # omitted with several is an error, a name picks the server, and an
        # unknown name errors — the uri is the only required argument
        runtime, ctx = runtime_factory({"one": inproc_entry(), "two": inproc_entry()})
        await connect(runtime, "one", make_resource_server("one-res", note="one's note"))
        await connect(runtime, "two", make_resource_server("two-res", note="two's note"))
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "server" in err.value.message
        result = await asyncio.wait_for(
            tool.run_async({"server": "two", "uri": "note://today"}), BOUND
        )
        assert result.content == "two's note"
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                tool.run_async({"server": "ghost", "uri": "note://today"}), BOUND
            )
        assert err.value.code == "mcp_error"
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "missing_param"

    async def test_a_read_and_a_listing_through_the_tools(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")

        # text: content carries it, details carry the facts around it
        result = await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert result.content == "ship it"
        assert result.details["uri"] == "note://today"
        assert result.details["mimeType"] == "text/plain"
        assert result.details["is_error"] is False
        assert "images" not in result.details
        assert "files" not in result.details

        # a template uri reads through the server
        result = await asyncio.wait_for(
            tool.run_async({"uri": "greeting://ada"}), BOUND
        )
        assert result.content == "hello ada"

        # an image lands in details with a placeholder in content
        result = await asyncio.wait_for(tool.run_async({"uri": "pic://logo"}), BOUND)
        assert "[image: image/png]" in result.content
        assert result.details["images"] == [
            {
                "type": "image",
                "data": base64.b64encode(PNG).decode(),
                "mimeType": "image/png",
            }
        ]

        # an opaque blob is spooled to a temp file
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

        # and every failure is an mcp error — a missing resource, another
        # server's uri, or a pre-2026 error code
        for args in ({"uri": "note://absent"}, {"uri": "other://server/thing"}):
            with pytest.raises(ToolError) as err:
                await asyncio.wait_for(tool.run_async(args), BOUND)
            assert err.value.code == "mcp_error"

        runtime, ctx = runtime_factory({"legacy": inproc_entry()})
        await connect(runtime, "legacy", legacy_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://gone"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "legacy" in err.value.message

        # the listing tools carry the entries and page through a cursor —
        # and an empty listing says so
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("list_mcp_resources")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert "today: note://today [text/plain] — today's note" in result.content
        assert "blob: blob://data [application/octet-stream]" in result.content
        assert [r["uri"] for r in result.details["resources"]] == [
            "note://today",
            "pic://logo",
            "blob://data",
        ]
        assert "nextCursor" not in result.details

        templates = ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(templates.run_async({"server": "demo"}), BOUND)
        assert [t["uriTemplate"] for t in result.details["resourceTemplates"]] == [
            "greeting://{name}"
        ]
        assert "greeting://{name}" in result.content

        runtime, ctx = runtime_factory({"paged": inproc_entry()})
        await connect(runtime, "paged", paged_server())
        templates = ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(templates.run_async({}), BOUND)
        assert result.content == "no resource templates"
        assert result.details["resourceTemplates"] == []

        tool = ctx.tools.get("list_mcp_resources")
        first = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert "first: note://first" in first.content
        assert first.details["nextCursor"] == "page-2"
        second = await asyncio.wait_for(
            tool.run_async({"cursor": first.details["nextCursor"]}), BOUND
        )
        assert "second: note://second" in second.content
        assert "nextCursor" not in second.details
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"cursor": 5}), BOUND)
        assert err.value.code == "invalid_type"


# ── tool registration and exposure, through the runtime ──────


class TestToolMapping:
    """The per-server tool registration and its call mapping — full names,
    schemas, tags, and where each result shape lands."""

    async def test_tools_register_with_full_names_and_schemas(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)

        registry = ctx.tools
        assert "mcp__demo__search" in registry
        tool = registry.get("mcp__demo__search")
        assert tool.availability == "both"  # default exposure: auto → direct
        assert tool.schema["required"] == ["q"]
        assert tool.tags == frozenset({"mcp", "mcp:demo"})
        assert tool.mcp == {"server": "demo", "tool": "search"}
        assert tool.mcp_raw_name == "search"
        assert tool.source == "host"  # a bare BuildContext stamps nothing
        assert "mcp__demo__search" in registry.names(audience="model")

        # a bare definition with no schema or description still registers,
        # and a colliding raw name gets the same stable suffix the naming
        # rules assign
        import hashlib

        bare, _ctx = runtime_factory({})
        session = McpSession(peer_config("x"))
        tool = mcp_tool(bare, session, "demo", {"name": "raw"}, "both", False)
        assert tool.schema == {"type": "object", "properties": {}}
        assert tool.description == "MCP tool raw from demo"
        bare.assignments["demo"] = assign_tool_names("demo", ["a-b", "a_b"])
        tool = mcp_tool(bare, session, "demo", {"name": "a_b"}, "both", False)
        assert tool.name == "mcp__demo__a_b_" + hashlib.sha1(b"a_b").hexdigest()[:6]
        assert tool.mcp_raw_name == "a_b"

        # the anchor tool carries the runtime, and it is program-only
        anchor, _ctx = runtime_factory({"off": inproc_entry(enabled=False)})
        status = mcp_status_tool(anchor)
        assert status.name == "mcp_status"
        assert status.availability == "program"
        assert status.mcp_runtime is anchor
        assert status.schema == {"type": "object", "properties": {}}

    async def test_calls_map_into_content_and_details(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)

        result = await asyncio.wait_for(
            ctx.tools.get("mcp__demo__search").run_async({"q": "hi"}), BOUND
        )
        assert result.content == "hello"
        assert result.details["server"] == "demo"
        assert result.details["tool"] == "search"
        assert result.details["structured_content"] == {"ok": True}
        assert result.details["is_error"] is False

        # an image block lands in details with a placeholder in content
        result = await asyncio.wait_for(
            ctx.tools.get("mcp__demo__pic").run_async({}), BOUND
        )
        assert result.content == "here:\n[image: image/png]"
        assert result.details["images"][0]["data"] == "QUJD"

        # an isError result raises the mcp error, message and all — and an
        # input-requiring answer has its own code
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                ctx.tools.get("mcp__demo__fail").run_async({}), BOUND
            )
        assert err.value.code == "mcp_error"
        assert "boom" in err.value.message
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                ctx.tools.get("mcp__demo__ask").run_async({}), BOUND
            )
        assert err.value.code == "mcp_input_required"

    async def test_the_tool_exposure_mapping(self, runtime_factory):
        # codemode exposure: registered, the program audience sees it, the
        # model does not
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="codemode")})
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert "mcp__demo__search" in registry.names(audience="program")
        assert "mcp__demo__search" not in registry.names(audience="model")

        # hidden: registered, visible to neither audience
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="hidden")})
        await peer_connect(runtime, "demo", MODERN_PEER)
        assert ctx.tools.get("mcp__demo__search") is not None
        assert "mcp__demo__search" not in ctx.tools.names(audience="model")
        assert "mcp__demo__search" not in ctx.tools.names(audience="program")

        # a per-tool override beats the server-level one
        runtime, ctx = runtime_factory(
            {
                "demo": inproc_entry(
                    exposure="codemode", toolExposure={"search": "direct", "pic": "hidden"}
                )
            }
        )
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert "mcp__demo__search" in registry.names(audience="model")
        assert "mcp__demo__ask" not in registry.names(audience="model")
        assert registry.get("mcp__demo__pic") is not None
        assert "mcp__demo__pic" not in registry.names(audience="model")

        # and the default follows the codemode plugin's presence
        runtime, ctx = runtime_factory({"demo": inproc_entry()}, codemode_enabled=True)
        await peer_connect(runtime, "demo", MODERN_PEER)
        assert runtime.default_exposure == "codemode"
        assert "mcp__demo__search" not in ctx.tools.names(audience="model")


class TestSyncTools:
    """A changed tool list reconciles the registry, and a connect that fails
    is remembered on the session rather than raised into the turn."""

    async def test_a_changed_tool_list_reconciles_the_registry(self, runtime_factory):
        """The modern tool-change subscription drives the runtime's own sync,
        so a server that adds a tool and drops another is reflected."""
        runtime, ctx = runtime_factory({"demo": {"command": "never-run"}})
        env = InProc()
        await connect(runtime, "demo", env.server)
        registry = ctx.tools
        assert "mcp__demo__greet" in registry

        env.add("late")
        assert await republish_until(
            env,
            lambda: "mcp__demo__late" in registry,
            what="the new tool to register",
        )

        env.remove("late")
        env.remove("greet")
        assert await republish_until(
            env,
            lambda: "mcp__demo__late" not in registry
            and "mcp__demo__greet" not in registry,
            what="the gone tools to unregister",
        )
        # the untouched tool stays exactly where the connect put it
        assert "mcp__demo__plain" in registry

    async def test_connect_failures_are_marked_never_raised(self, runtime_factory):
        """The runtime's own bounded connect: a server that cannot start, or
        never answers, is reported and remembered on the session."""
        runtime, ctx = runtime_factory({"bad": {"command": "no-such-binary-mocode"}})
        try:
            await asyncio.wait_for(runtime.start(), BOUND)  # must not raise
            status = {s["name"]: s for s in runtime.status()}
            assert status["bad"]["state"] == "error"
            assert status["bad"]["error"]
            assert not [n for n in ctx.tools.names() if n.startswith("mcp__")]
        finally:
            runtime.shutdown()

        # the same accounting on a connect that times out instead — the
        # same policy a spawned-but-silent child triggers
        runtime, ctx = runtime_factory(
            {"slow": {"command": "never-run"}}, connect_timeout_s=0.2
        )
        session = McpSession(
            runtime.config["slow"],
            server=WirePeer({"server/discover": Silent(), "initialize": Silent()}),
        )
        runtime.sessions["slow"] = session
        try:
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await asyncio.wait_for(session.connect_and_register(), 0.2)
        finally:
            session.shutdown()
        runtime.shutdown()
        assert session.state != STATE_CONNECTED


# ── the modern tool-change subscription ──────────────────────


def _greet(name: str) -> str:
    return f"hi {name}"


def _late() -> str:
    return "late"


def _plain() -> str:
    return "plain"


def inproc_config(name: str = "demo") -> McpServerConfig:
    """The config shape an in-process session needs — the ``server=`` seam
    overrides the target, so only the bookkeeping matters."""
    return McpServerConfig(name=name, source="test")


class InProc:
    """An in-process modern server plus the bus that publishes its changes."""

    def __init__(self) -> None:
        self.bus = InMemorySubscriptionBus()
        self.server = MCPServer(
            name="inproc", version="9.9.9", instructions="In-proc.", subscriptions=self.bus
        )
        self.server.add_tool(_greet, name="greet", description="Say hi")
        self.server.add_tool(_plain, name="plain")

    def add(self, name: str) -> None:
        self.server.add_tool(_late, name=name, description="Arrived later")

    def remove(self, name: str) -> None:
        self.server.remove_tool(name)

    async def announce(self, times: int = 1) -> None:
        """Publish the tool-list-changed cue; a change published before the
        stream acknowledged is lost, so the caller re-publishes (identical
        events coalesce while unconsumed) until the reconciliation lands."""
        for _ in range(times):
            await self.bus.publish(ToolsListChanged())


async def republish_until(env: InProc, predicate, *, what: str) -> bool:
    """Announce the change until the subscription delivers it — one
    identical event coalesces while unconsumed, so this is idempotent.

    The *waiting* is the conftest ``wait_until`` poll; the announcing is a
    driver task alongside it, so neither half is a bare sleep.
    """
    stop = asyncio.Event()

    async def announce_forever() -> None:
        while not stop.is_set():
            await env.announce()
            await settle(0.02)

    driver = asyncio.ensure_future(announce_forever())
    try:
        return await wait_until(predicate, bound=BOUND, what=what)
    finally:
        stop.set()
        driver.cancel()
        await asyncio.gather(driver, return_exceptions=True)


class TestSubscriptionLifecycle:
    async def test_the_watch_dies_with_the_session(self, runtime_factory):
        """A change delivered through the subscription reconciles the
        registry; once the connection is gone — closed or shut down — the
        same announcement no longer moves anything: the watch went down
        with it."""
        runtime, ctx = runtime_factory({"demo": {"command": "never-run"}})
        env = InProc()
        session = await connect(runtime, "demo", env.server)
        registry = ctx.tools

        env.add("late")
        assert await republish_until(
            env, lambda: "mcp__demo__late" in registry, what="the new tool to register"
        )

        # a graceful close ends the watch: the cue is still publishable but
        # no session answers it any more
        await asyncio.wait_for(session.close(), BOUND)
        assert session.state == STATE_CLOSED
        registry.unregister("mcp__demo__late")
        await env.announce(3)
        assert "mcp__demo__late" not in registry

        # a sync shutdown ends it the same way, on its own session
        runtime, ctx = runtime_factory({"demo": {"command": "never-run"}})
        env = InProc()
        session = await connect(runtime, "demo", env.server)

        env.add("late")
        assert await republish_until(
            env, lambda: "mcp__demo__late" in ctx.tools, what="the new tool to register"
        )

        session.shutdown()
        assert session.state == STATE_CLOSED
        ctx.tools.unregister("mcp__demo__late")
        await env.announce(3)
        assert "mcp__demo__late" not in ctx.tools

        # a modern session with nobody to answer a change starts no watch —
        # announcing cannot move any registry, so the only observable is
        # that the session stays healthy
        env = InProc()
        session = McpSession(inproc_config(), server=env.server)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        await env.announce(3)
        assert session.state == STATE_CONNECTED
        await asyncio.wait_for(session.close(), BOUND)

# ── the retry driver — a scripted stub of the SDK stream ────


async def _noop() -> None:
    """A change callback that answers by doing nothing."""


class _ScriptedStream:
    """One stubbed ``subscriptions/listen`` stream: it yields *events* and
    then ends — ``"lost"`` as an abrupt drop, ``"closed"`` as the server's
    graceful close."""

    def __init__(self, events: list, end: str) -> None:
        self._events = list(events)
        self._end = end

    async def __aenter__(self) -> "_ScriptedStream":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def __aiter__(self) -> "_ScriptedStream":
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        if self._end == "lost":
            from mcp.client.subscriptions import SubscriptionLost

            raise SubscriptionLost("the stub's stream dropped")
        raise StopAsyncIteration


class _ScriptedClient:
    """The slice of the SDK client ``watch_tools`` needs: ``listen()`` opens
    the next scripted stream — one script entry per attempt, the last
    repeating — and records when and with which filter, so a test reads the
    backoff off it."""

    def __init__(self, scripts: list[tuple[list, str]]) -> None:
        self._scripts = scripts
        self.attempts = 0
        self.filters: list[dict] = []
        self.opened_at: list[float] = []

    def listen(self, **filters):
        self.attempts += 1
        self.filters.append(dict(filters))
        self.opened_at.append(asyncio.get_running_loop().time())
        index = min(self.attempts - 1, len(self._scripts) - 1)
        events, end = self._scripts[index]
        return _ScriptedStream(events, end)

    @property
    def gaps(self) -> list[float]:
        """Seconds between successive listen attempts."""
        return [b - a for a, b in zip(self.opened_at, self.opened_at[1:])]


class _RefusingClient:
    """An SDK client whose ``listen()`` refuses with *error* outright."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def listen(self, **filters):
        self.calls += 1
        raise self.error


async def drive(client, on_changed, *, report=None) -> asyncio.Task:
    """``watch_tools`` in its own task — the way the session runs it — ready
    to cancel."""
    task = asyncio.create_task(watch_tools(client, on_changed, report=report))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return task


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class _InstantBackoff:
    """The subscriptions module's sleep entry, swapped for a recording one.

    ``watch_tools`` sleeps through its module-level ``asyncio`` name, so
    replacing that name — and only that name, with ``CancelledError`` still
    the real one — turns the product's backoff pauses into recorded values:
    the wait the product asked for is asserted instead of spent.

    What this bypasses is the *wall clock* of a pause, nothing else: the
    sequence of waits (the initial value, the reset on an event, the
    doubling toward the ceiling) is the backoff rule itself and is asserted
    exactly, off the product's own constants.
    """

    def __init__(self) -> None:
        self.waited: list[float] = []

    def patch(self, monkeypatch) -> None:
        from mocode.host.plugin.builtin.mcp import subscriptions

        async def sleep(seconds: float) -> None:
            self.waited.append(seconds)
            # one loop turn, so the watch's retry loop stays cooperative
            # with the test's own polling — the pause is *recorded*, not spent
            await _REAL_ASYNCIO_SLEEP(0)

        shim = SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError)
        monkeypatch.setattr(subscriptions, "asyncio", shim)


def _recorder(seen: list):
    """A change callback that answers by recording that it ran."""

    async def on_changed() -> None:
        seen.append(1)

    return on_changed


class TestWatchTools:
    async def test_a_stream_event_drives_the_change_callback(self):
        client = _ScriptedClient([([ToolsListChanged()], "closed")])
        seen: list[int] = []

        task = await drive(client, _recorder(seen))
        assert await wait_until(lambda: seen, what="the change callback")
        await _cancel(task)
        # the filter is tools-only: nothing else on the modern vocabularies
        assert client.filters and client.filters[0] == {"tools_list_changed": True}

    async def test_a_drop_or_a_close_re_listens_after_the_backoff(self, monkeypatch):
        backoff = _InstantBackoff()
        backoff.patch(monkeypatch)
        # an abrupt drop and a graceful close are the same policy: neither
        # replays, so both re-listen — and both wait the backoff first
        for end in ("lost", "closed"):
            backoff.waited.clear()
            client = _ScriptedClient([([], end), ([ToolsListChanged()], "closed")])
            seen: list[int] = []
            reports: list[str] = []
            task = await drive(client, _recorder(seen), report=reports.append)
            assert await wait_until(
                lambda: len(seen) >= 2, what=f"the re-listened stream's event ({end})"
            )
            await _cancel(task)
            assert client.attempts >= 3, end  # the end cost a re-listen
            assert backoff.waited and backoff.waited[0] == BACKOFF_INITIAL, end
            if end == "lost":
                assert any("dropped" in message for message in reports), end

    async def test_the_backoff_doubles_and_an_event_resets_it(self, monkeypatch):
        backoff = _InstantBackoff()
        backoff.patch(monkeypatch)
        client = _ScriptedClient(
            [([], "lost"), ([ToolsListChanged()], "lost"), ([], "lost")]
        )
        seen: list[int] = []

        task = await drive(client, _recorder(seen), report=lambda _message: None)
        assert await wait_until(lambda: client.attempts >= 4, what="four attempts")
        await _cancel(task)
        # the first wait is the initial one; the event that arrived reset the
        # doubling to it, and the next loss doubled it before the ceiling
        assert backoff.waited[:3] == [
            BACKOFF_INITIAL,
            BACKOFF_INITIAL,
            2 * BACKOFF_INITIAL,
        ]

    async def test_failures_are_reported_and_the_two_diagnoses_are_raised(self):
        # a pre-2026 server refuses the subscription: raised, never retried
        from mcp.client.subscriptions import ListenNotSupportedError

        client = _RefusingClient(ListenNotSupportedError("2025-11-25"))
        with pytest.raises(ListenNotSupportedError):
            await asyncio.wait_for(watch_tools(client, _noop), BOUND)
        assert client.calls == 1

        # a refused request is reported and then raised — retrying it here
        # would only fight the connection task already unwinding
        client = _RefusingClient(MCPError(-32601, "the server refuses subscriptions"))
        reports: list[str] = []
        with pytest.raises(MCPError):
            await asyncio.wait_for(
                watch_tools(client, _noop, report=reports.append), BOUND
            )
        assert client.calls == 1
        assert reports  # said before it was raised

        # a re-sync that fails mid-stream is reported and the stream keeps
        # watching — the next event retries it
        client = _ScriptedClient([([ToolsListChanged(), ToolsListChanged()], "closed")])
        reports = []
        calls: list[int] = []

        async def on_changed():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("the registry hiccuped")

        task = await drive(client, on_changed, report=reports.append)
        assert await wait_until(lambda: len(calls) >= 2, what="the retried re-sync")
        await _cancel(task)
        assert len(reports) == 1 and "registry hiccuped" in reports[0]


# ── plugin lifecycle ────────────────────────────────────────


def _plugins_mcp(tmp_path: Path, servers: dict, **extra: Any) -> dict:
    """The ``plugins`` block a plugin_host takes for the mcp plugin."""
    codemode_enabled = extra.pop("codemode_enabled", False)
    block: dict[str, Any] = {"mcp": {"servers": servers, **extra}}
    if codemode_enabled:
        block["codemode"] = {"enabled": True}
    return block


def _table(tmp_path: Path, **servers: Any) -> dict:
    """A servers table for ``plugins.mcp.servers`` — every entry here points
    at an interpreter that is never launched, because the tests below wire
    in-process servers into the runtime's own slots instead."""
    return {
        name: {"command": "python", "args": ["-c", "pass"], **extra}
        for name, extra in servers.items()
    }


def _status_rows(text: str) -> dict[str, tuple[str, int | None]]:
    """Parse a rendered section into ``name -> (how the server is reached)``.

    The prompt and the status report are user-facing text, so what a test
    asserts is the *structure* they carry — never their wording.
    """
    rows: dict[str, tuple[str, int | None]] = {}
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        body = line[2:]
        name, _, rest = body.partition(": ")
        if not name or not rest:
            continue
        rows[name] = (rest.split(" ", 1)[0], None)
    return rows


def _tool_lines(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """The indented ``tools:`` catalogues of the section, by server name.

    Answers ``(catalogues, tails)``: the tool names a connected server lists,
    and — when a list was cut short — the ``+N more`` pointer that followed
    it. Both are structure a test can assert without pinning the wording.
    """
    catalogues: dict[str, list[str]] = {}
    tails: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("- "):
            current = line[2:].partition(": ")[0]
        elif line.startswith("  tools: ") and current is not None:
            body = line[len("  tools: "):]
            head, marker, tail = body.partition(" … +")
            catalogues[current] = head.split(", ")
            if marker:
                tails[current] = " … +" + tail
    return catalogues, tails


def _peer_sessions(monkeypatch, servers: dict[str, object]) -> None:
    """Substitute the session factory at the runtime's module boundary.

    ``McpRuntime.start()`` builds one ``McpSession`` per configured server
    and connects them — the lifecycle under test. A test that needs those
    sessions to talk to an in-process server (or a wire peer) instead of a
    spawned child hands the peers over here: the factory is the collaborator
    being replaced, and ``start()`` still runs in full.

    The real class is taken from the module that defines it, not from the
    runtime module's current binding, so patching twice in one test (two
    hosts on one monkeypatch) wraps the class, not the wrapper.
    """
    from mocode.host.plugin.builtin.mcp import client as client_module
    from mocode.host.plugin.builtin.mcp import runtime as runtime_module

    real = client_module.McpSession

    def factory(config, **kwargs):
        peer = servers.get(config.name)
        if peer is None:
            return real(config, **kwargs)
        return real(config, server=peer, **kwargs)

    monkeypatch.setattr(runtime_module, "McpSession", factory)


#: The per-server keys ``_materialize_with_servers`` accepts — the rest are
#: plugin-level settings that belong in the mcp block instead.
_SERVER_KEYS = ("exposure", "toolExposure", "enabled", "description", "timeout")


async def _materialize_with_servers(
    plugin_host, monkeypatch, tmp_path: Path, servers: dict[str, object], **mcp_extra: Any
) -> tuple[Any, McpRuntime, BuildContext]:
    """Materialize a host whose mcp plugin connects to in-process servers.

    *servers* maps the configured server name to the in-process server (or
    wire peer) its slot takes; the config entries are placeholders that are
    never launched. The plugin's own ``prepare()`` runs — bounded connects,
    registration, the prompt section — so what is under test is the real
    lifecycle. The runtime is reached again through the registry, the way
    the plugin's own ``close()`` finds it.
    """
    _peer_sessions(monkeypatch, servers)
    entries = {
        name: {key: mcp_extra.pop(key) for key in _SERVER_KEYS if key in mcp_extra}
        for name in servers
    }
    host = plugin_host(
        plugins=[PLUGIN],
        build=True,
        assemble=True,
        config_kwargs={"plugins": _plugins_mcp(tmp_path, _table(tmp_path, **entries), **mcp_extra)},
    )
    await asyncio.wait_for(host.materialize(), BOUND)
    runtime = host.ctx.tools.get("mcp_status").mcp_runtime
    return host, runtime, host.ctx


class TestPluginLifecycle:
    def test_build_registers_the_anchor_tool_with_the_runtime(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=False)
        assert not host.failures
        status = host.ctx.tools.get("mcp_status")
        assert status is not None
        assert isinstance(status.mcp_runtime, McpRuntime)
        # program-only: the model is never offered the anchor
        assert "mcp_status" not in host.ctx.tools.names(audience="model")
        assert "mcp_status" in host.ctx.tools.names(audience="program")

        # the instance is stateless and the package surface is what a plugin
        # author imports
        assert McpPlugin().name == "mcp"
        assert (
            McpPlugin().description == "Connect to MCP servers and expose their tools"
        )
        assert PLUGIN.name == "mcp"
        from mocode.host.plugin.builtin.mcp import (
            PLUGIN as exported,
            McpRuntime as exported_runtime,
        )

        assert isinstance(exported, McpPlugin)
        assert exported.name == "mcp"
        assert exported_runtime is McpRuntime


class TestPromptSection:
    """The mcp_servers prompt section — structure, not wording: which
    servers are listed, how each is reached, and which tool names a
    connected one catalogues."""

    async def test_a_connected_server_joins_the_section(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"alpha": WirePeer(MODERN_PEER)},
            default_exposure="direct",
        )
        section = next(s for s in ctx.prompt_sections if s.name == "mcp_servers")
        assert section.priority == 46
        assert section.derived_from == "tools"
        assert section.pinned is False
        assert ctx.agent.system_prompt.count("<mcp_servers>") == 1

        text = section.render({})
        rows = _status_rows(text)
        assert "alpha" in rows
        assert rows["alpha"][0] == "direct"
        # the D12 catalogue: raw names on an indented continuation line
        catalogues, tails = _tool_lines(text)
        assert catalogues["alpha"] == ["ask", "fail", "pic", "search"]
        assert "alpha" not in tails  # no cut, no pointer
        # a server with no configured description falls back to the first
        # line of the instructions it handed over the wire
        assert "Wire server instructions." in text
        host.close()

        # past thirty names the list gives up counting and points at
        # search_tools() — the truncated raws stay out of the section
        tools = [
            {
                "name": f"tool_{i:02d}",
                "description": f"Tool {i:02d}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for i in range(40)
        ]
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host,
            monkeypatch,
            tmp_path,
            {
                "many": WirePeer(
                    {
                        **MODERN_PEER,
                        "tools/list": {
                            "resultType": "complete",
                            "tools": tools,
                            "ttlMs": 0,
                            "cacheScope": "public",
                        },
                    }
                )
            },
        )
        text = next(s for s in ctx.prompt_sections if s.name == "mcp_servers").render({})
        catalogues, tails = _tool_lines(text)
        catalogue = catalogues["many"]
        assert len(catalogue) == 30  # the list holds exactly thirty names
        assert catalogue[0] == "tool_00" and catalogue[-1] == "tool_29"
        assert "tool_30" not in text and "tool_39" not in text
        assert tails["many"].startswith(" … +10 more ")
        host.close()

        # and with no servers configured there is no section at all
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        assert "<mcp_servers>" not in host.ctx.agent.system_prompt
        assert (
            next(
                s for s in host.ctx.prompt_sections if s.name == "mcp_servers"
            ).render({})
            == ""
        )
        host.close()

    async def test_servers_that_are_not_shown_stay_out_or_one_line(
        self, plugin_host, monkeypatch, tmp_path
    ):
        # hidden and disabled servers never make a row — hidden still
        # registers its tools, just unreachable by either audience
        peers = {"shown": WirePeer(MODERN_PEER), "hid": WirePeer(MODERN_PEER)}
        _peer_sessions(monkeypatch, peers)
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": _plugins_mcp(
                    tmp_path,
                    _table(
                        tmp_path, shown={}, hid={"exposure": "hidden"}, off={"enabled": False}
                    ),
                )
            },
        )
        await asyncio.wait_for(host.materialize(), BOUND)
        registry = host.ctx.tools
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        rows = _status_rows(text)
        assert "shown" in rows
        assert "hid" not in text  # hidden skips the whole row
        assert "off" not in text  # disabled never connects
        # hidden still registered — just unreachable by either audience
        assert registry.get("mcp__hid__search") is not None
        assert "mcp__hid__search" not in registry.names(audience="model")
        assert "mcp__hid__search" not in registry.names(audience="program")
        host.close()

        # No catalogue without a connection: a still-connecting server keeps
        # the plain one-line form and no ``tools:`` line at all.
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": _plugins_mcp(
                    tmp_path, _table(tmp_path, slow={}), connect_timeout_s=0.2
                )
            },
        )
        # the connect is bounded by the runtime's own timeout, and the
        # session stays unconnected — exactly the shape the section reads
        runtime = host.ctx.tools.get("mcp_status").mcp_runtime
        session = McpSession(
            runtime.config["slow"],
            server=WirePeer({"server/discover": Silent(), "initialize": Silent()}),
        )
        runtime.sessions["slow"] = session
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(session.connect_and_register(), 0.2)
        await asyncio.wait_for(host.materialize(), BOUND)

        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "slow" in _status_rows(text)
        assert "tools:" not in text
        host.close()


class TestCodemodeNotice:
    """The one-shot codemode warning is an event on the channel — the
    observable behaviour, not a flag on the runtime."""

    async def test_program_only_tools_warn_once_and_codemode_suppresses_it(
        self, plugin_host, monkeypatch, tmp_path
    ):
        # codemode off: the warning travels with the connect — waited for on
        # the channel, not slept for and counted after the fact
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)},
            exposure="codemode",
        )
        reader = ctx.subscribe(since=0)
        warnings = await _first_warning(reader)
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        # the count travels as a leading number — parsed, not spelled out
        assert int(warnings[0].message.split(" ", 1)[0]) == 4
        # one conversation, one warning — a second emission never lands
        assert [e for e in await _drain(reader) if isinstance(e, Notice)] == []
        host.close()

        # codemode on: the runtime returns before it can emit
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host,
            monkeypatch,
            tmp_path,
            {"demo": WirePeer(MODERN_PEER)},
            exposure="codemode",
            codemode_enabled=True,
        )
        reader = ctx.subscribe(since=0)
        await _drain(reader)
        assert [e for e in await _drain(reader) if isinstance(e, Notice)] == []
        host.close()


async def _drain(reader) -> list:
    """Everything a subscriber has so far — read, not polled."""
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return seen


async def _first_warning(reader) -> list:
    """The codemode warnings on the channel, waiting for the first one.

    The reader is drained inside the poll, so a warning that arrives while
    this waits is seen the moment it does — no guessed window, and the
    drain is one read rather than a second pass that finds nothing.
    """
    deadline = asyncio.get_running_loop().time() + BOUND
    while True:
        for event in await _drain(reader):
            if isinstance(event, Notice) and "reachable only through codemode" in event.message:
                return [event]
        if asyncio.get_running_loop().time() >= deadline:
            return []
        await settle(0.01)


# ── end to end through the dispatcher ───────────────────────


async def _dispatch(host, name: str, args: dict, *, origin: str = "model"):
    """One call through the real dispatcher pipeline; events come back too."""
    events: list = []

    async def publish(event, *, fold: bool) -> None:
        events.append(event)

    dispatcher = ToolDispatcher(host.ctx.tools, HookRunner(), AgentConfig(), publish)
    result = await asyncio.wait_for(dispatcher.run(name, args, origin=origin), BOUND)
    return result, events


class TestEndToEnd:
    """One call through the real dispatcher pipeline — what the model can
    reach, what only a program origin can, and what refuses both."""

    async def test_the_model_reaches_a_direct_tool_through_the_dispatcher(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)}
        )
        result, events = await _dispatch(host, "mcp__demo__search", {"q": "x"})
        assert result.status == "ok"
        assert result.content == "hello"
        assert result.details["structured_content"] == {"ok": True}
        started = [e for e in events if isinstance(e, ToolCallStarted)]
        finished = [e for e in events if isinstance(e, ToolCallFinished)]
        assert len(started) == len(finished) == 1
        assert finished[0].status == "ok"
        host.close()

    async def test_origin_and_exposure_decide_what_reaches_a_tool(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host,
            monkeypatch,
            tmp_path,
            {"demo": WirePeer(MODERN_PEER)},
            exposure="codemode",
        )

        # a program call (what a codemode script would make) succeeds
        result, _ = await _dispatch(host, "mcp__demo__search", {"q": "x"}, origin="program")
        assert result.status == "ok"
        # the model's origin cannot see or run it
        assert "mcp__demo__search" not in ctx.tools.names(audience="model")
        result, _ = await _dispatch(host, "mcp__demo__search", {"q": "x"})
        assert result.status == "denied"
        # the anchor answers program calls too
        result, _ = await _dispatch(host, "mcp_status", {}, origin="program")
        assert result.status == "ok"
        assert result.details["servers"][0]["name"] == "demo"
        result, _ = await _dispatch(host, "mcp_status", {})
        assert result.status == "denied"
        host.close()

        # a hidden server's tool refuses both origins — the switch is off
        # for the model and the program alike
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)}, exposure="hidden"
        )
        for origin in ("model", "program"):
            result, _ = await _dispatch(
                host, "mcp__demo__search", {"q": "x"}, origin=origin
            )
            assert result.status == "denied", origin
        host.close()

# ── the loopback HTTP transports ─────────────────────────────


#: One tool, enough to prove listing, calling and the response shape.
TOOLS = [
    {
        "name": "greet",
        "description": "Say hi",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }
]

#: The session id the legacy streamable-HTTP handshake hands out.
SESSION_ID = "legacy-session-7"
#: The endpoint event a legacy SSE server announces first — its session
#: id rides the query.
MESSAGE_PATH = "/messages?sessionId=sse-session-42"


def _result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _discover_result() -> dict:
    """A modern ``server/discover`` answer — the SDK requires the result
    type, the supported versions and the cache fields at 2026-07-28."""
    return {
        "resultType": "complete",
        "supportedVersions": ["2026-07-28"],
        "capabilities": {"tools": {}},
        "_meta": {
            "io.modelcontextprotocol/serverInfo": {"name": "http-srv", "version": "1.0"}
        },
        "instructions": "HTTP server instructions.",
        "ttlMs": 0,
        "cacheScope": "public",
    }


def _initialize_result() -> dict:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "http-legacy-srv", "version": "1.0"},
        "instructions": "Legacy HTTP server instructions.",
    }


def _tools_list_result(*, modern: bool) -> dict:
    result: dict[str, Any] = {"tools": TOOLS}
    if modern:
        result.update({"resultType": "complete", "ttlMs": 0, "cacheScope": "public"})
    return result


def _call_result(message: dict, *, modern: bool) -> dict:
    arguments = (message.get("params") or {}).get("arguments") or {}
    text = f"hi {arguments.get('name', '')}"
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"text": text},
    }
    if modern:
        result["resultType"] = "complete"
    return result


class _FakeServer(ThreadingHTTPServer):
    """A threaded stdlib server that knows which fake it serves."""

    fake: _FakeEndpoint


class _FakeEndpoint:
    """A fake MCP endpoint on a loopback port, served in a daemon thread.

    ``style`` picks the wire the fake speaks:

    * ``json`` — streamable HTTP, modern era, every response a JSON body;
    * ``sse`` — streamable HTTP, modern era, every response an SSE frame;
    * ``legacy`` — streamable HTTP that answers the modern probe with a
      plain error (so the client falls back to the ``initialize``
      handshake), hands out a ``Mcp-Session-Id`` and keeps the optional
      GET stream open;
    * ``sse-transport`` — the legacy 2024-11-05 HTTP+SSE wire: a GET
      stream that announces the POST endpoint first, with every answer
      riding the stream.

    Every request is recorded (path, headers, message) so a test can
    assert what really reached the server.
    """

    def __init__(self, *, style: str) -> None:
        assert style in ("json", "sse", "legacy", "sse-transport")
        self.style = style
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stream: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stopped = threading.Event()
        self._server = _FakeServer(("127.0.0.1", 0), partial(_Handler))
        self._server.fake = self
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        path = "/sse" if self.style == "sse-transport" else "/mcp"
        return f"http://127.0.0.1:{self._server.server_address[1]}{path}"

    def start(self) -> _FakeEndpoint:
        self._thread = threading.Thread(
            # a short poll interval is the fake's own pacing: shutdown then
            # costs a fiftieth of a second instead of the stdlib's half
            # second, and no request ever waits on it
            target=self._server.serve_forever,
            args=(0.05,),
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopped.set()
        self._server.shutdown()
        self._server.server_close()

    # ── what the handler threads share ─────────────────────

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def stopped_event(self) -> threading.Event:
        """The event a handler thread waits on instead of sleeping."""
        return self._stopped

    @property
    def stream(self) -> queue.Queue[tuple[str, str]]:
        """Frames waiting to be written to the legacy SSE stream."""
        return self._stream

    def record(self, path: str, headers: Any, message: dict | None) -> None:
        with self._lock:
            self.requests.append(
                {
                    "path": path,
                    "headers": {str(k).lower(): str(v) for k, v in headers.items()},
                    "message": message,
                }
            )

    def answer(self, message: dict) -> dict:
        """The JSON-RPC reply for one request *message*."""
        method, rid = message.get("method"), message.get("id")
        if method == "server/discover" and self.style in ("json", "sse"):
            return _result(rid, _discover_result())
        if method == "initialize":
            return _result(rid, _initialize_result())
        if method == "tools/list":
            return _result(rid, _tools_list_result(modern=self.style in ("json", "sse")))
        if method == "tools/call":
            return _result(rid, _call_result(message, modern=self.style in ("json", "sse")))
        # a legacy server answers the modern probe with a plain error,
        # which is exactly what makes the client fall back to the handshake
        return _error(rid, -32601, f"method not found: {method}")


class _Handler(BaseHTTPRequestHandler):
    """One request against the fake: JSON, SSE-framed, or SSE transport."""

    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:
        pass  # keep the test output quiet

    @property
    def fake(self) -> _FakeEndpoint:
        return self.server.fake  # type: ignore[attr-defined]

    # ── writers ────────────────────────────────────────────

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_frame(self, event: str, data: str) -> None:
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ── verbs ──────────────────────────────────────────────

    def do_GET(self) -> None:
        fake = self.fake
        if fake.style == "sse-transport" and self.path.startswith("/sse"):
            self._serve_stream()
        elif fake.style == "legacy" and self.path.startswith("/mcp"):
            self._serve_legacy_get_stream()
        else:
            self.send_error(404)

    def _serve_stream(self) -> None:
        """The legacy SSE transport's GET stream: announce the POST
        endpoint (its session id rides the query), then forward every
        answer the POST side queues — until the client goes away."""
        fake = self.fake
        fake.record(self.path, self.headers, None)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self._send_frame("endpoint", MESSAGE_PATH)
            while True:
                try:
                    event, data = fake.stream.get(timeout=0.2)
                except queue.Empty:
                    if fake.stopped:
                        return
                    continue
                self._send_frame(event, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _serve_legacy_get_stream(self) -> None:
        """A streamable-HTTP server's optional legacy GET stream: open,
        kept alive with comments until the client hangs up."""
        fake = self.fake
        fake.record(self.path, self.headers, None)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            while not fake.stopped:
                self.wfile.write(b": keep-alive\n\n")
                # pacing the fake's own keep-alive is the fake's behaviour,
                # not a wait on a condition — it waits on the stop event,
                # which the teardown sets.
                fake.stopped_event.wait(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self) -> None:
        fake = self.fake
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            message = json.loads(body)
        except ValueError:
            self.send_error(400)
            return
        fake.record(self.path, self.headers, message)
        if "id" not in message:
            # a notification: accepted, and nothing comes back
            self._send(202, b"", "text/plain")
            return
        reply = fake.answer(message)
        if fake.style == "sse-transport":
            # the answer rides the GET stream, the POST is only receipt
            fake.stream.put(("message", json.dumps(reply)))
            self._send(202, b"", "text/plain")
            return
        if fake.style == "sse":
            # the same answer, framed as one SSE event
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self._send_frame("message", json.dumps(reply))
            return
        extra = (
            {"Mcp-Session-Id": SESSION_ID}
            if fake.style == "legacy" and message.get("method") == "initialize"
            else None
        )
        self._send(200, json.dumps(reply).encode("utf-8"), "application/json", extra)

    def do_DELETE(self) -> None:
        """The streamable-HTTP transport's session termination."""
        self.fake.record(self.path, self.headers, None)
        self._send(204, b"", "text/plain")


# ── the seams ─────────────────────────────────────────


def http_config(
    url: str,
    *,
    transport: str = "streamable-http",
    headers: dict[str, str] | None = None,
    name: str = "demo",
) -> McpServerConfig:
    """A session config for an HTTP *url* — the fields :mod:`.config`
    parses out of an entry, assembled as directly as a test can."""
    return McpServerConfig(
        name=name,
        transport=transport,
        url=url,
        headers=headers or {},
        source="test",
    )


def _refused_url() -> str:
    """A loopback URL with nothing behind it — a port just vacated."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp"


@pytest.fixture
def _loopback_only(monkeypatch: Any) -> None:
    """Keep every request on the loopback: a system proxy (Windows reads
    one from the registry) would otherwise answer for the fake endpoints
    and for a dead port alike, making the outcome machine-dependent and
    letting a request leave the machine at all."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")


@pytest.fixture
def endpoint() -> Iterator[Any]:
    """Start fake endpoints on demand; every one is torn down after."""
    started: list[_FakeEndpoint] = []

    def factory(style: str) -> _FakeEndpoint:
        fake = _FakeEndpoint(style=style).start()
        started.append(fake)
        return fake

    yield factory
    for fake in started:
        fake.stop()


@pytest_asyncio.fixture
async def http_sessions() -> Any:
    """Build McpSessions over HTTP configs; close them all on teardown."""
    created: list[McpSession] = []

    def factory(cfg: McpServerConfig, **kwargs) -> McpSession:
        session = McpSession(cfg, **kwargs)
        created.append(session)
        return session

    yield factory
    for session in created:
        try:
            await asyncio.wait_for(session.close(), BOUND)
        except Exception:
            session.shutdown()


@pytest.mark.usefixtures("_loopback_only")
class TestStreamableHttp:
    async def test_the_modern_streamable_http_wire(self, endpoint, http_sessions):
        # modern JSON answers, the configured headers on every request, and
        # the same answers arriving SSE-framed
        fake = endpoint("json")
        session = http_sessions(
            http_config(
                fake.url,
                headers={"Authorization": "Bearer test-token", "X-Mcp-Test": "yes"},
            )
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "http-srv", "version": "1.0"}
        assert session.instructions == "HTTP server instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(
            session.call_tool("greet", {"name": "mocode"}), BOUND
        )
        assert result["content"] == [{"type": "text", "text": "hi mocode"}]
        assert result["structuredContent"] == {"text": "hi mocode"}
        posts = [r for r in fake.requests if r["message"] is not None]
        assert posts
        for request in posts:
            assert request["headers"].get("authorization") == "Bearer test-token"
            assert request["headers"].get("x-mcp-test") == "yes"

        fake = endpoint("sse")
        session = http_sessions(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(
            session.call_tool("greet", {"name": "sse"}), BOUND
        )
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        assert result["structuredContent"] == {"text": "hi sse"}


@pytest.mark.usefixtures("_loopback_only")
class TestLegacyOverHttp:
    async def test_the_two_legacy_http_wires(self, endpoint, http_sessions):
        # a streamable-HTTP server that answers the modern probe with a
        # plain error: the client falls back to the handshake, and the
        # session id it is given rides every later request
        fake = endpoint("legacy")
        session = http_sessions(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "http-legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy HTTP server instructions."
        posts = [r for r in fake.requests if r["message"] is not None]
        assert [r["message"].get("method") for r in posts[:2]] == [
            "server/discover",
            "initialize",
        ]
        # the session the handshake was given rides every later request
        later = posts[2:]
        assert later
        for request in later:
            assert request["headers"].get("mcp-session-id") == SESSION_ID

        # the legacy 2024-11-05 HTTP+SSE wire: a GET stream that announces
        # the POST endpoint first, with every answer riding the stream
        fake = endpoint("sse-transport")
        session = http_sessions(
            http_config(
                fake.url,
                transport="sse",
                headers={"Authorization": "Bearer sse-token"},
            )
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(
            session.call_tool("greet", {"name": "sse"}), BOUND
        )
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        gets = [r for r in fake.requests if r["message"] is None]
        posts = [r for r in fake.requests if r["message"] is not None]
        assert gets and gets[0]["path"] == "/sse"
        # configured headers reach the GET stream as well as the POSTs
        assert gets[0]["headers"].get("authorization") == "Bearer sse-token"
        # the endpoint event's session id rides every message POST
        assert posts and all("sessionId=sse-session-42" in r["path"] for r in posts)


@pytest.mark.usefixtures("_loopback_only")
class TestConnectionFailures:
    async def test_a_refused_connection_is_a_transport_error(self, http_sessions):
        session = http_sessions(http_config(_refused_url()))
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_transport"
        assert session.state == STATE_ERROR
        assert session.last_error
