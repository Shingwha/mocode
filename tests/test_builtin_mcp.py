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


def write_mcp_json(path: Path, data: dict | str) -> Path:
    """Write one mcp.json — a dict is dumped, a str written verbatim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    return path


def stdio_entry(command: str = "tool", **extra) -> dict:
    return {"type": "stdio", "command": command, **extra}

class TestLoadServers:
    def test_no_configured_servers_yields_an_empty_table(self, tmp_path):
        assert load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home") == {}

    def test_inline_servers_from_config_json(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"demo": {"command": "run"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["demo"]
        assert merged["demo"].command == "run"
        assert merged["demo"].source == "config.json"

    def test_project_and_home_files_are_read(self, tmp_path):
        home = tmp_path / "home"
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"mcpServers": {"proj": stdio_entry()}})
        write_mcp_json(home / "mcp.json", {"mcpServers": {"user": stdio_entry()}})
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=home)
        assert set(merged) == {"proj", "user"}

    def test_a_project_entry_replaces_a_home_entry_wholesale(self, tmp_path):
        home = tmp_path / "home"
        write_mcp_json(
            home / "mcp.json",
            {"mcpServers": {"demo": stdio_entry("low", env={"K": "V"}, timeout=5)}},
        )
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": {"demo": stdio_entry("high", args=["a"])}},
        )
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=home)
        assert len(merged) == 1
        cfg = merged["demo"]
        assert cfg.command == "high"
        # whole-entry replacement: nothing bleeds through from the loser
        assert cfg.args == ["a"]
        assert cfg.env == {}
        assert cfg.timeout is None

    def test_inline_beats_project_beats_home_beats_plugin(self, tmp_path):
        home = tmp_path / "home"
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(home / "mcp.json", {"mcpServers": {"s": stdio_entry("home")}})
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json", {"mcpServers": {"s": stdio_entry("proj")}}
        )
        plugin_mcp = {"$schema": MCP_SCHEMA_1_0_0, "mcpServers": {"s": stdio_entry("plug")}}
        write_mcp_json(plugin_dir / "mcp.json", plugin_mcp)

        merged = load_servers(mcp_config={}, cwd=tmp_path, home=home, plugin_sources=[plugin_dir])
        assert merged["s"].command == "proj"

        merged = load_servers(
            mcp_config={"servers": {"s": {"command": "inline"}}},
            cwd=tmp_path,
            home=home,
            plugin_sources=[plugin_dir],
        )
        assert merged["s"].command == "inline"

    def test_server_names_differing_only_in_dash_and_underscore_are_one(self, tmp_path, capsys):
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
        bad = {
            "not-an-object": 42,
            "no-command": {"type": "stdio"},
            "empty-command": stdio_entry("  "),
            "bad-args": stdio_entry(args="nope"),
            "bad-args-item": stdio_entry(args=[1]),
            "bad-env": stdio_entry(env=["K"]),
            "bad-env-value": stdio_entry(env={"K": 1}),
            "bad-cwd": stdio_entry(cwd=3),
            "bad-type": {"type": "websocket", "command": "x"},
            "good": stdio_entry(),
        }
        merged = load_servers(
            mcp_config={"servers": bad}, cwd=tmp_path, home=tmp_path / "home"
        )
        assert list(merged) == ["good"]
        err = capsys.readouterr().err
        for name in ("not-an-object", "no-command", "bad-args", "bad-env", "bad-type"):
            assert name in err

    def test_sse_entries_parse_as_the_sse_transport(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"old": {"type": "sse", "url": "https://x/sse"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        cfg = merged["old"]
        assert cfg.transport == "sse"
        assert cfg.url == "https://x/sse"
        assert cfg.headers == {}

    def test_streamable_http_entries_parse(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "web": {"type": "streamable-http", "url": "http://localhost/mcp"},
                    "web2": {"url": "http://localhost/mcp"},
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["web", "web2"]
        for cfg in merged.values():
            assert cfg.transport == "streamable-http"
            assert cfg.url == "http://localhost/mcp"
            assert cfg.headers == {}

    def test_type_is_optional_in_mocode_files_and_inferred(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"a": {"command": "x"}, "b": {"url": "https://u/mcp"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["a", "b"]
        assert merged["a"].transport == "stdio"
        assert merged["a"].command == "x"
        assert merged["b"].transport == "streamable-http"
        assert merged["b"].url == "https://u/mcp"
        assert merged["b"].headers == {}

    def test_http_is_the_streamable_http_transport(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"web": {"type": "http", "url": "https://u/mcp"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert merged["web"].transport == "streamable-http"

    def test_url_and_header_values_expand_in_mocode_files(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "web": {
                        "type": "streamable-http",
                        "url": "https://${MCP_TEST_HOST}/mcp",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                    }
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={"MCP_TEST_HOST": "mcp.example", "MCP_TEST_TOKEN": "secret"},
        )
        cfg = merged["web"]
        assert cfg.url == "https://mcp.example/mcp"
        assert cfg.headers == {"Authorization": "Bearer secret"}

    def test_a_missing_variable_in_a_header_value_reports_and_empties(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "web": {
                        "url": "https://h/mcp",
                        "headers": {"X-Token": "${MCP_TEST_MISSING}"},
                    }
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={},
        )
        assert merged["web"].headers == {"X-Token": ""}
        assert "MCP_TEST_MISSING" in capsys.readouterr().err

    def test_loopback_urls_may_stay_http(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "v4": {"type": "sse", "url": "http://127.0.0.1:9000/sse"},
                    "v6": {"type": "sse", "url": "http://[::1]:9000/sse"},
                    "name": {"type": "sse", "url": "http://localhost/sse"},
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["v4", "v6", "name"]
        assert merged["v6"].url == "http://[::1]:9000/sse"

    def test_http_extensions_apply_to_http_entries_too(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "web": {
                        "type": "streamable-http",
                        "url": "https://u/mcp",
                        "enabled": False,
                        "timeout": 12,
                        "exposure": "hidden",
                        "description": "one line",
                    }
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        cfg = merged["web"]
        assert cfg.enabled is False
        assert cfg.timeout == 12.0
        assert cfg.exposure == "hidden"
        assert cfg.description == "one line"

    def test_invalid_http_entries_are_skipped_without_hurting_valid_ones(self, tmp_path, capsys):
        bad = {
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
            "good": {"type": "streamable-http", "url": "https://u/mcp"},
        }
        merged = load_servers(
            mcp_config={"servers": bad}, cwd=tmp_path, home=tmp_path / "home"
        )
        assert list(merged) == ["good"]
        err = capsys.readouterr().err
        for name in bad:
            if name != "good":
                assert name in err
class TestMocodeExtensions:
    def test_env_vars_expand_from_the_environment(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "demo": stdio_entry(env={"TOKEN": "${MCP_TEST_TOKEN}"}, args=["${MCP_TEST_ARG}"])
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={"MCP_TEST_TOKEN": "secret", "MCP_TEST_ARG": "value"},
        )
        cfg = merged["demo"]
        assert cfg.env == {"TOKEN": "secret"}
        assert cfg.args == ["value"]

    def test_a_missing_variable_expands_to_empty_and_reports(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={"servers": {"demo": stdio_entry(env={"TOKEN": "${MCP_MISSING_VAR}"})}},
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={},
        )
        assert merged["demo"].env == {"TOKEN": ""}
        assert "MCP_MISSING_VAR" in capsys.readouterr().err

    def test_bang_command_is_reported_and_kept_literal(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={"servers": {"demo": stdio_entry(env={"K": "!echo hi"})}},
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={},
        )
        assert merged["demo"].env == {"K": "!echo hi"}
        assert "'!command'" in capsys.readouterr().err

    def test_plugin_tokens_are_meaningless_in_mocode_files(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "a": stdio_entry(args=["${PLUGIN_ROOT}/x"]),
                    "b": stdio_entry(env={"K": "${PLUGIN_DATA}"}),
                    "c": stdio_entry(cwd="${PLUGIN_ROOT}"),
                    "good": stdio_entry(),
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
            environ={},
        )
        assert list(merged) == ["good"]
        assert "only mean something inside a plugin" in capsys.readouterr().err

    def test_extensions_are_kept_on_the_config(self, tmp_path):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "demo": {
                        "command": "x",
                        "enabled": False,
                        "timeout": 12,
                        "exposure": "hidden",
                        "toolExposure": {"a": "direct"},
                        "description": "one line",
                    }
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        cfg = merged["demo"]
        assert cfg.enabled is False
        assert cfg.timeout == 12.0
        assert cfg.exposure == "hidden"
        assert cfg.tool_exposure == {"a": "direct"}
        assert cfg.description == "one line"

    def test_bad_extension_values_report_and_fall_back(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={
                "servers": {
                    "demo": {
                        "command": "x",
                        "enabled": "yes",
                        "timeout": "later",
                        "exposure": 3,
                        "toolExposure": ["a"],
                        "description": {},
                    }
                }
            },
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        cfg = merged["demo"]
        assert cfg.enabled is True
        assert cfg.timeout is None
        assert cfg.exposure is None
        assert cfg.tool_exposure == {}
        assert cfg.description == ""
        err = capsys.readouterr().err
        for word in ("'enabled'", "'timeout'", "'exposure'", "'toolExposure'", "'description'"):
            assert word in err

    def test_relative_cwd_resolves_against_the_file_directory(self, tmp_path):
        project = tmp_path / "proj"
        write_mcp_json(
            project / ".mocode" / "mcp.json",
            {"mcpServers": {"demo": stdio_entry(cwd="./data")}},
        )
        write_mcp_json(
            tmp_path / "home" / "mcp.json",
            {"mcpServers": {"other": stdio_entry(cwd="sub")}},
        )
        home = tmp_path / "home"
        merged = load_servers(mcp_config={}, cwd=project, home=home)
        assert merged["demo"].cwd == str((project / ".mocode" / "data").resolve())
        assert merged["other"].cwd == str((home / "sub").resolve())

    def test_absolute_cwd_is_kept(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"demo": stdio_entry(cwd=str(tmp_path))}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert merged["demo"].cwd == str(tmp_path)

    def test_broken_json_file_is_reported_and_skipped(self, tmp_path, capsys):
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", "{not json")
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home")
        assert merged == {}
        assert "unreadable mcp.json" in capsys.readouterr().err

    def test_mcp_servers_must_be_an_object(self, tmp_path, capsys):
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"mcpServers": ["a"]})
        merged = load_servers(mcp_config={}, cwd=tmp_path, home=tmp_path / "home")
        assert merged == {}
        assert "must be an object" in capsys.readouterr().err
class TestPluginFileRules:
    def _plugin_mcp(self, servers: dict, **top) -> dict:
        return {"$schema": MCP_SCHEMA_1_0_0, "mcpServers": servers, **top}

    def test_a_valid_plugin_file_is_loaded(self, tmp_path):
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

    def test_the_plugin_data_directory_is_created(self, tmp_path):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": stdio_entry()}))
        load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert (plugin_dir / PLUGIN_DATA_DIR).is_dir()

    def test_a_missing_or_mismatched_schema_skips_the_whole_file(self, tmp_path, capsys):
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
        merged = load_servers(
            mcp_config={},
            cwd=tmp_path,
            home=tmp_path / "home",
            plugin_sources=[plugin_dir, other_dir],
        )
        assert merged == {}
        err = capsys.readouterr().err
        assert err.count("MCP configuration is skipped") == 2

    def test_unknown_top_level_fields_skip_the_whole_file(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json", self._plugin_mcp({"srv": stdio_entry()}, extra=1)
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert merged == {}
        assert "unknown top-level field" in capsys.readouterr().err

    def test_plugin_placeholders_expand_once(self, tmp_path):
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

    def test_expanded_cwd_must_stay_inside_its_root(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp(
                {
                    "escapes-root": stdio_entry(cwd="${PLUGIN_ROOT}/../out"),
                    "escapes-data": stdio_entry(cwd="${PLUGIN_DATA}/../../out"),
                    "abs-outside": stdio_entry(cwd="/tmp"),
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

    def test_env_must_not_set_plugin_root_or_plugin_data(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp({"bad": stdio_entry(env={"PLUGIN_ROOT": "/x"})}),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert merged == {}
        assert "must not set 'PLUGIN_ROOT'" in capsys.readouterr().err

    def test_a_bad_cwd_shape_is_rejected(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp({"bad": stdio_entry(cwd="sub/dir")}),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert merged == {}
        assert "must start with './'" in capsys.readouterr().err

    def test_extension_keys_are_reported_and_ignored_in_plugin_files(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        entry = stdio_entry(exposure="hidden", timeout=5, description="nope")
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": entry}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        cfg = merged["srv"]
        assert cfg.exposure is None
        assert cfg.timeout is None
        assert cfg.description == ""
        assert "unknown field(s)" in capsys.readouterr().err

    def test_type_is_required_in_plugin_files(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(plugin_dir / "mcp.json", self._plugin_mcp({"srv": {"command": "x"}}))
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert merged == {}
        assert "missing 'type'" in capsys.readouterr().err

    def test_a_valid_http_entry_is_loaded(self, tmp_path):
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
                }
            ),
        )
        merged = load_servers(
            mcp_config={}, cwd=tmp_path, home=tmp_path / "home", plugin_sources=[plugin_dir]
        )
        assert list(merged) == ["web", "feed"]
        assert merged["web"].transport == "streamable-http"
        assert merged["web"].url == "http://127.0.0.1:8080/mcp"
        assert merged["web"].headers == {"Authorization": "Bearer t"}
        assert merged["feed"].transport == "sse"
        assert merged["feed"].url == "https://u/sse"

    def test_http_entries_expand_nothing_in_plugin_files(self, tmp_path, capsys):
        plugin_dir = tmp_path / "plugins" / "acme"
        write_mcp_json(
            plugin_dir / "mcp.json",
            self._plugin_mcp(
                {
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
        assert list(merged) == ["good"]
        err = capsys.readouterr().err
        assert err.count("entry skipped") == 5
        assert "${VAR}" in err
class TestNormalize:
    def test_everything_outside_alnum_and_underscore_folds(self):
        assert normalize("dev-radius") == "dev_radius"
        assert normalize("a b.c/d:e") == "a_b_c_d_e"
        assert normalize("already_ok") == "already_ok"
        assert normalize("UPPER-1") == "UPPER_1"

    def test_server_names_differing_only_in_separator_fold_equal(self):
        assert fold_server_name("my-server") == fold_server_name("my_server")
        assert fold_server_name("a.b") == fold_server_name("a-b")
        assert fold_server_name("ab") != fold_server_name("a-b")

    def test_full_tool_names(self):
        assert tool_full_name("dev-radius", "search") == "mcp__dev_radius__search"
        assert tool_full_name("srv", "do-thing") == "mcp__srv__do_thing"


class TestAssignToolNames:
    def test_no_collision_maps_raw_names_directly(self):
        out = assign_tool_names("srv", ["search", "get_one"])
        assert out == {
            "search": "mcp__srv__search",
            "get_one": "mcp__srv__get_one",
        }

    def test_colliding_names_get_a_stable_hash_suffix(self):
        import hashlib

        out = assign_tool_names("srv", ["a-b", "a_b", "a b"])
        assert set(out) == {"a-b", "a_b", "a b"}
        # sorted: "a b" < "a-b" < "a_b" — the first keeps the plain name
        assert out["a b"] == "mcp__srv__a_b"
        assert out["a-b"] == "mcp__srv__a_b_" + hashlib.sha1(b"a-b").hexdigest()[:6]
        assert out["a_b"] == "mcp__srv__a_b_" + hashlib.sha1(b"a_b").hexdigest()[:6]

    def test_the_assignment_does_not_depend_on_listing_order(self):
        names = ["x-1", "x_1", "plain", "y z", "y-z"]
        forward = assign_tool_names("srv", names)
        backward = assign_tool_names("srv", list(reversed(names)))
        assert forward == backward


class TestDefaultExposure:
    def test_auto_follows_the_codemode_plugin(self):
        assert default_exposure({}, codemode_enabled=False) == "direct"
        assert default_exposure({}, codemode_enabled=True) == "codemode"
        assert default_exposure({"default_exposure": "auto"}, codemode_enabled=True) == "codemode"

    def test_an_explicit_value_is_used_directly(self):
        assert default_exposure({"default_exposure": "hidden"}, codemode_enabled=True) == "hidden"
        assert default_exposure({"default_exposure": "codemode-deferred"}, codemode_enabled=False) == "codemode-deferred"

    def test_garbage_reports_and_falls_back_to_auto(self, capsys):
        assert default_exposure({"default_exposure": 7}, codemode_enabled=False) == "direct"
        assert default_exposure({"default_exposure": "bogus"}, codemode_enabled=True) == "codemode"
        err = capsys.readouterr().err
        assert "default_exposure" in err

    def test_non_dict_config_is_auto(self):
        assert default_exposure([], codemode_enabled=False) == "direct"


def _cfg(**kwargs) -> McpServerConfig:
    kwargs.setdefault("name", "demo")
    kwargs.setdefault("command", "x")
    kwargs.setdefault("source", "test")
    return McpServerConfig(**kwargs)


class TestResolveExposure:
    def test_server_exposure_overrides_the_default(self):
        cfg = _cfg(exposure="hidden")
        assert resolve_exposure(cfg, "anything", "direct") == "hidden"

    def test_no_server_exposure_uses_the_default(self):
        assert resolve_exposure(_cfg(), "anything", "codemode") == "codemode"

    def test_exact_tool_name_beats_pattern_and_server(self):
        cfg = _cfg(
            exposure="hidden",
            tool_exposure={"search": "direct", "get_*": "codemode"},
        )
        assert resolve_exposure(cfg, "search", "hidden") == "direct"
        assert resolve_exposure(cfg, "get_one", "hidden") == "codemode"
        assert resolve_exposure(cfg, "delete_one", "hidden") == "hidden"

    def test_star_matches_any_characters_and_first_pattern_wins(self):
        cfg = _cfg(tool_exposure={"*_x": "direct", "get_*": "hidden"})
        assert resolve_exposure(cfg, "get_x", "codemode") == "direct"
        cfg = _cfg(tool_exposure={"a*c": "direct"})
        assert resolve_exposure(cfg, "aanythingc", "codemode") == "direct"
        assert resolve_exposure(cfg, "aanythingcX", "codemode") == "codemode"

    def test_codemode_deferred_alias(self):
        assert canon_exposure("codemode-deferred") == "codemode"
        cfg = _cfg(exposure="codemode-deferred")
        assert resolve_exposure(cfg, "t", "direct") == "codemode"

    def test_unknown_values_report_and_fall_through(self, capsys):
        cfg = _cfg(exposure="sideways", tool_exposure={"t": "also-bogus"})
        assert resolve_exposure(cfg, "t", "direct") == "direct"
        cfg = _cfg(tool_exposure={"other": "bogus"})
        assert resolve_exposure(cfg, "other", "codemode") == "codemode"
        err = capsys.readouterr().err
        assert "sideways" in err and "bogus" in err

    def test_unknown_default_falls_back_to_direct(self, capsys):
        assert resolve_exposure(_cfg(), "t", "zzz") == "direct"
        assert "zzz" in capsys.readouterr().err

    def test_availability_mapping(self):
        assert availability_for("direct") == ("both", False)
        assert availability_for("codemode") == ("program", False)
        assert availability_for("deferred") == ("program", False)
        assert availability_for("hidden") == ("both", True)


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


class TestModernSession:
    async def test_the_negotiated_era_and_the_session_facts(self, session_factory):
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "wire-srv", "version": "1.0"}
        assert session.instructions == "Wire server instructions."
        assert session.state == STATE_CONNECTED
        assert session.last_error is None
        assert session.server_capabilities is not None
        assert session.server_capabilities.tools is not None

    async def test_connect_hands_the_runtime_the_wire_form_tools(self, session_factory):
        connected: list[list[dict]] = []

        async def on_connected(session, tools):
            connected.append(tools)

        session = session_factory(WirePeer(MODERN_PEER), on_connected=on_connected)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        tools = session.tools
        assert [t["name"] for t in tools] == ["search", "fail", "ask", "pic"]
        assert len(connected) == 1 and connected[0] == tools
        by_name = {t["name"]: t for t in tools}
        assert by_name["search"]["description"] == "Search things"
        schema = by_name["search"]["inputSchema"]
        assert schema["type"] == "object"
        assert "q" in schema["properties"]  # a JSON Schema object node
        assert schema["required"] == ["q"]

    async def test_call_results_come_back_as_wire_form_dicts(self, session_factory):
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "hello"}]
        assert result["structuredContent"] == {"ok": True}

    async def test_a_slow_call_times_out(self, session_factory):
        routes = dict(MODERN_PEER)
        routes["tools/call"] = Late(5.0, _call_answer("search"))
        session = session_factory(WirePeer(routes), name="slow", timeout=0.4)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert err.value.code == "mcp_timeout"
        assert session.last_error and "timed out" in session.last_error

    async def test_input_required_raises_its_own_error(self, session_factory):
        session = session_factory(
            WirePeer({**MODERN_PEER, "tools/call": _call_answer("ask")}), name="ask"
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("ask"), BOUND)
        assert err.value.code == "mcp_input_required"
        assert "elicitation is not supported" in str(err.value)

    async def test_non_text_content_blocks_pass_through(self, session_factory):
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

    async def test_a_dropped_peer_disconnects_and_the_session_marks_it(
        self, session_factory
    ):
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

    async def test_a_call_after_close_is_refused(self, session_factory):
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.close(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert err.value.code == "mcp_closed"


class TestLegacySession:
    async def test_the_handshake_negotiates_the_legacy_era(self, session_factory):
        session = session_factory(
            WirePeer({**LEGACY_PEER, "initialize": LEGACY_HANDSHAKE}), name="legacy",
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "wire-legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy wire instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["search", "fail", "ask", "pic"]

    async def test_calls_come_back_as_wire_form_dicts(self, session_factory):
        routes = {
            **LEGACY_PEER,
            "initialize": LEGACY_HANDSHAKE,
            "tools/call": {
                "content": [{"type": "text", "text": "echo:{'x': '1'}"}],
                "structuredContent": {"legacy": True},
            },
        }
        session = session_factory(WirePeer(routes), name="legacy2")
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("search"), BOUND)
        assert result["content"][0]["text"] == "echo:{'x': '1'}"
        assert result["structuredContent"] == {"legacy": True}
        assert result["isError"] is False


class TestEraNegotiation:
    async def test_a_modern_server_negotiates_the_current_version(self, session_factory):
        session = session_factory(WirePeer(MODERN_PEER))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"

    async def test_an_unanswered_discover_falls_back_to_the_handshake(
        self, session_factory
    ):
        """A server that does not answer the modern probe is legacy — the
        standard forbids deciding the era on a single error code."""
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

    async def test_a_server_sharing_no_version_fails_the_connection(
        self, session_factory
    ):
        session = session_factory(
            WirePeer({**MODERN_PEER, "server/discover": Unsupported(["2026-08-30"])}),
            name="neg3",
        )
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_error"
        assert session.state == STATE_ERROR
        assert session.last_error

    async def test_a_silent_peer_fails_the_connect_under_a_bound(self, session_factory):
        session = session_factory(
            WirePeer({"server/discover": Silent(), "initialize": Silent()}), name="neg4"
        )
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(session.connect_and_register(), 2)
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


class TestResourceMethods:
    """The session's resource pass-throughs, wire-form — the tools built on
    them (below) only split contents and map errors."""

    async def test_listing_resources_and_templates_is_wire_form(self, session_factory):
        session = session_factory(make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.server_capabilities.resources is not None

        resources = await asyncio.wait_for(session.list_resources(), BOUND)
        entries = resources["resources"]
        assert [r["uri"] for r in entries] == [
            "note://today",
            "pic://logo",
            "blob://data",
        ]
        assert entries[0]["name"] == "today"
        assert entries[0]["mimeType"] == "text/plain"

        templates = await asyncio.wait_for(session.list_resource_templates(), BOUND)
        assert [t["uriTemplate"] for t in templates["resourceTemplates"]] == [
            "greeting://{name}"
        ]

    async def test_reading_a_resource_returns_its_contents(self, session_factory):
        session = session_factory(make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.read_resource("greeting://ada"), BOUND)
        assert result["contents"] == [
            {
                "uri": "greeting://ada",
                "mimeType": "text/plain",
                "text": "hello ada",
            }
        ]

    async def test_reading_a_missing_resource_is_an_mcp_error(self, session_factory):
        session = session_factory(make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.read_resource("note://absent"), BOUND)
        assert err.value.code == "mcp_error"


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


class TestRegistration:
    async def test_a_resource_server_registers_the_three_tools(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())

        registry = ctx.tools
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
        runtime, ctx = runtime_factory({"plain": inproc_entry()})
        session = await connect(
            runtime, "plain", LowLevelServer("plain-low", on_list_tools=_list_tools_empty)
        )
        assert session.server_capabilities is not None
        assert session.server_capabilities.resources is None

        registry = ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert name not in registry
        # the per-server tool path is untouched — an empty listing registers nothing
        assert [n for n in registry.names() if n.startswith("mcp__")] == []

    async def test_reconciling_twice_keeps_the_same_tool_objects(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        first = ctx.tools.get("read_mcp_resource")

        runtime._apply_resource_tools()

        assert ctx.tools.get("read_mcp_resource") is first

    async def test_no_resource_server_left_and_the_tools_go_away(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        session = await connect(runtime, "demo", make_resource_server())
        assert "read_mcp_resource" in ctx.tools

        session.state = STATE_DISCONNECTED
        runtime._apply_resource_tools()

        for name in RESOURCE_TOOL_NAMES:
            assert name not in ctx.tools

    async def test_a_late_resource_server_registers_on_its_connect(
        self, runtime_factory
    ):
        runtime, ctx = runtime_factory(
            {"plain": inproc_entry(), "demo": inproc_entry()}
        )
        await connect(
            runtime, "plain", LowLevelServer("plain-late", on_list_tools=_list_tools_empty)
        )
        assert "read_mcp_resource" not in ctx.tools

        await connect(runtime, "demo", make_resource_server())
        assert "read_mcp_resource" in ctx.tools


class TestExposure:
    async def test_the_default_exposure_offers_the_tools_to_the_model(
        self, runtime_factory
    ):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "both"

    async def test_codemode_keeps_them_program_only(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()}, codemode_enabled=True)
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "program"

    async def test_a_configured_server_exposure_is_honoured(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="codemode")})
        await connect(runtime, "demo", make_resource_server())
        assert ctx.tools.get("read_mcp_resource").availability == "program"

    async def test_the_widest_exposure_wins(self, runtime_factory):
        runtime, ctx = runtime_factory(
            {
                "readonly": inproc_entry(exposure="codemode"),
                "direct": inproc_entry(exposure="direct"),
            }
        )
        await connect(runtime, "readonly", make_resource_server("readonly"))
        assert ctx.tools.get("read_mcp_resource").availability == "program"

        await connect(runtime, "direct", make_resource_server("direct"))
        assert ctx.tools.get("read_mcp_resource").availability == "both"

    async def test_a_hidden_server_registers_the_tools_switched_off(
        self, runtime_factory
    ):
        """A hidden server's resources stay invisible to both audiences —
        the same availability_for pipeline a hidden server tool takes."""
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="hidden")})
        await connect(runtime, "demo", make_resource_server())

        registry = ctx.tools
        for name in RESOURCE_TOOL_NAMES:
            assert registry.get(name) is not None  # registered, like a hidden tool
            assert name not in registry.names(audience="model")
            assert name not in registry.names(audience="program")

    async def test_a_switched_off_resource_tool_refuses_to_run(self, runtime_factory):
        """The dispatcher is the one execution path: a disabled tool's run is
        refused there, whatever the Tool object itself would do."""
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="hidden")})
        await connect(runtime, "demo", make_resource_server())

        result = await asyncio.wait_for(
            _dispatcher(ctx.tools).run(
                "read_mcp_resource", {"uri": "note://today"}, origin="program"
            ),
            BOUND,
        )
        assert result.status == "denied"
        assert "switched off" in result.content

    async def test_going_from_offered_to_switched_off(self, runtime_factory):
        """The reconciliation runs both ways: the direct server goes away and
        only the hidden one is left — the set re-registers switched off."""
        runtime, ctx = runtime_factory(
            {
                "direct": inproc_entry(),
                "hidden": inproc_entry(exposure="hidden"),
            }
        )
        direct = await connect(runtime, "direct", make_resource_server("direct-res"))
        await connect(runtime, "hidden", make_resource_server("hidden-res"))
        assert "read_mcp_resource" in ctx.tools.names(audience="model")

        direct.state = STATE_DISCONNECTED
        runtime._apply_resource_tools()

        assert ctx.tools.get("read_mcp_resource") is not None
        assert "read_mcp_resource" not in ctx.tools.names(audience="model")
        assert "read_mcp_resource" not in ctx.tools.names(audience="program")

    async def test_a_late_direct_server_brings_the_tools_back(self, runtime_factory):
        """Switched off, then a direct server connects — the reconciled set
        re-registers enabled (the disable goes with the old form)."""
        runtime, ctx = runtime_factory(
            {"hidden": inproc_entry(exposure="hidden"), "direct": inproc_entry()}
        )
        await connect(runtime, "hidden", make_resource_server("hidden-res"))
        assert "read_mcp_resource" not in ctx.tools.names(audience="program")

        await connect(runtime, "direct", make_resource_server("direct-res"))
        assert ctx.tools.get("read_mcp_resource").availability == "both"
        assert "read_mcp_resource" in ctx.tools.names(audience="model")


class TestServerArgument:
    async def test_omitted_with_one_server_is_that_server(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert result.content == "ship it"
        assert result.details["server"] == "demo"  # the configured name

    async def test_omitted_with_several_servers_errors(self, runtime_factory):
        runtime, ctx = runtime_factory(
            {"one": inproc_entry(), "two": inproc_entry()}
        )
        await connect(runtime, "one", make_resource_server("one-res"))
        await connect(runtime, "two", make_resource_server("two-res"))
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "server" in err.value.message

    async def test_a_name_picks_the_server(self, runtime_factory):
        runtime, ctx = runtime_factory(
            {"one": inproc_entry(), "two": inproc_entry()}
        )
        await connect(runtime, "one", make_resource_server("one-res", note="one's note"))
        await connect(runtime, "two", make_resource_server("two-res", note="two's note"))
        tool = ctx.tools.get("read_mcp_resource")

        result = await asyncio.wait_for(
            tool.run_async({"server": "two", "uri": "note://today"}), BOUND
        )
        assert result.content == "two's note"
        assert result.details["server"] == "two"

    async def test_an_unknown_server_errors(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                tool.run_async({"server": "ghost", "uri": "note://today"}), BOUND
            )
        assert err.value.code == "mcp_error"

    async def test_a_dashed_name_matches_folded_or_verbatim(self, runtime_factory):
        """The prompt section shows the configured name (``my-server``) and
        the tool namespace shows the folded one (``mcp__my_server__…``) —
        either spelling picks the server."""
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
        assert folded.details["server"] == "my-server"

    async def test_uri_is_the_only_required_argument(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "missing_param"


class TestReadResource:
    async def test_text_lands_in_content(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(tool.run_async({"uri": "note://today"}), BOUND)
        assert result.content == "ship it"
        assert result.details["uri"] == "note://today"
        assert result.details["mimeType"] == "text/plain"
        assert result.details["is_error"] is False
        assert "images" not in result.details
        assert "files" not in result.details

    async def test_a_template_uri_reads_through_the_server(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        result = await asyncio.wait_for(
            tool.run_async({"uri": "greeting://ada"}), BOUND
        )
        assert result.content == "hello ada"

    async def test_an_image_lands_in_details(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
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
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
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
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://absent"}), BOUND)
        assert err.value.code == "mcp_error"

    async def test_a_uri_of_another_server_is_an_mcp_error(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(
                tool.run_async({"uri": "other://server/thing"}), BOUND
            )
        assert err.value.code == "mcp_error"

    async def test_the_legacy_error_code_maps_to_mcp_error(self, runtime_factory):
        """A pre-2026 read answers ``-32002``; the session maps it onto the
        same category a modern protocol error produces."""
        runtime, ctx = runtime_factory({"legacy": inproc_entry()})
        await connect(runtime, "legacy", legacy_server())
        tool = ctx.tools.get("read_mcp_resource")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"uri": "note://gone"}), BOUND)
        assert err.value.code == "mcp_error"
        assert "legacy" in err.value.message


class TestListing:
    async def test_resources_list_name_uri_and_mime(self, runtime_factory):
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

    async def test_an_empty_listing_says_so(self, runtime_factory):
        runtime, ctx = runtime_factory({"paged": inproc_entry()})
        await connect(runtime, "paged", paged_server())
        tool = ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert result.content == "no resource templates"
        assert result.details["resourceTemplates"] == []

    async def test_templates_list_their_uri_template(self, runtime_factory):
        runtime, ctx = runtime_factory({"withres": inproc_entry()})
        await connect(runtime, "withres", make_resource_server())
        tool = ctx.tools.get("list_mcp_resource_templates")
        result = await asyncio.wait_for(tool.run_async({"server": "withres"}), BOUND)
        assert "greeting://{name}" in result.content
        assert [t["uriTemplate"] for t in result.details["resourceTemplates"]] == [
            "greeting://{name}"
        ]

    async def test_the_cursor_passes_through_and_next_cursor_comes_back(
        self, runtime_factory
    ):
        runtime, ctx = runtime_factory({"paged": inproc_entry()})
        await connect(runtime, "paged", paged_server())
        tool = ctx.tools.get("list_mcp_resources")

        first = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert "first: note://first" in first.content
        assert first.details["nextCursor"] == "page-2"

        second = await asyncio.wait_for(
            tool.run_async({"cursor": first.details["nextCursor"]}), BOUND
        )
        assert "second: note://second" in second.content
        assert "nextCursor" not in second.details

    async def test_a_non_string_cursor_is_rejected(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        tool = ctx.tools.get("list_mcp_resources")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({"cursor": 5}), BOUND)
        assert err.value.code == "invalid_type"


class TestStatusReport:
    async def test_the_status_report_is_unchanged(self, runtime_factory):
        """What mcp_status renders must not move because resources exist —
        the resource tools are one per conversation, not per server."""
        runtime, _ctx = runtime_factory({"demo": inproc_entry()})
        await connect(runtime, "demo", make_resource_server())
        assert runtime.status() == [
            {"name": "demo", "state": "connected", "tools": 0, "error": None}
        ]

# ── tool registration and exposure, through the runtime ──────


class TestToolMapping:
    async def test_tools_register_with_full_names_and_schemas(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)

        registry = ctx.tools
        assert "mcp__demo__search" in registry
        tool = registry.get("mcp__demo__search")
        assert tool.availability == "both"  # default exposure: auto → direct (no codemode)
        assert tool.schema["required"] == ["q"]
        assert tool.tags == frozenset({"mcp", "mcp:demo"})
        assert tool.mcp == {"server": "demo", "tool": "search"}
        assert tool.mcp_raw_name == "search"
        assert tool.source == "host"  # a bare BuildContext stamps nothing

    async def test_a_successful_call_maps_into_content_and_details(
        self, runtime_factory
    ):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)
        tool = ctx.tools.get("mcp__demo__search")

        result = await asyncio.wait_for(tool.run_async({"q": "hi"}), BOUND)
        assert result.content == "hello"
        assert result.details["server"] == "demo"
        assert result.details["tool"] == "search"
        assert result.details["structured_content"] == {"ok": True}
        assert result.details["is_error"] is False

    async def test_an_is_error_result_raises_mcp_error(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)
        tool = ctx.tools.get("mcp__demo__fail")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "mcp_error"
        assert "boom" in err.value.message

    async def test_input_required_raises_its_own_code(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)
        tool = ctx.tools.get("mcp__demo__ask")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "mcp_input_required"

    async def test_image_blocks_land_in_details_with_a_placeholder(
        self, runtime_factory
    ):
        runtime, ctx = runtime_factory({"demo": inproc_entry()})
        await peer_connect(runtime, "demo", MODERN_PEER)
        tool = ctx.tools.get("mcp__demo__pic")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert result.content == "here:\n[image: image/png]"
        assert result.details["images"][0]["data"] == "QUJD"

    async def test_missing_schema_and_description_fall_back(self, runtime_factory):
        runtime, _ctx = runtime_factory({})
        session = McpSession(peer_config("x"))
        tool = mcp_tool(runtime, session, "demo", {"name": "raw"}, "both", False)
        assert tool.schema == {"type": "object", "properties": {}}
        assert tool.description == "MCP tool raw from demo"

    async def test_collision_gets_the_hash_suffix_and_raw_name(self, runtime_factory):
        import hashlib

        runtime, _ctx = runtime_factory({})
        session = McpSession(peer_config("x"))
        runtime.assignments["demo"] = assign_tool_names("demo", ["a-b", "a_b"])
        raw = {"name": "a_b"}
        tool = mcp_tool(runtime, session, "demo", raw, "both", False)
        assert tool.name == "mcp__demo__a_b_" + hashlib.sha1(b"a_b").hexdigest()[:6]
        assert tool.mcp_raw_name == "a_b"

    def test_mcp_status_tool_holds_the_runtime(self, runtime_factory):
        runtime, _ctx = runtime_factory({"off": inproc_entry(enabled=False)})
        tool = mcp_status_tool(runtime)
        assert tool.name == "mcp_status"
        assert tool.availability == "program"
        assert tool.mcp_runtime is runtime
        assert tool.schema == {"type": "object", "properties": {}}


class TestExposureMapping:
    async def test_codemode_exposure_is_program_only(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="codemode")})
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert "mcp__demo__search" in registry
        assert "mcp__demo__search" not in registry.names(audience="model")
        assert "mcp__demo__search" in registry.names(audience="program")

    async def test_hidden_exposure_registers_then_disables(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry(exposure="hidden")})
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert registry.get("mcp__demo__search") is not None  # registered
        assert "mcp__demo__search" not in registry.names(audience="model")
        assert "mcp__demo__search" not in registry.names(audience="program")

    async def test_tool_exposure_overrides_per_tool(self, runtime_factory):
        runtime, ctx = runtime_factory(
            {
                "demo": inproc_entry(
                    exposure="codemode",
                    toolExposure={"search": "direct", "pic": "hidden"},
                )
            }
        )
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert "mcp__demo__search" in registry.names(audience="model")
        assert "mcp__demo__ask" not in registry.names(audience="model")
        assert registry.get("mcp__demo__pic") is not None
        assert "mcp__demo__pic" not in registry.names(audience="model")

    async def test_default_exposure_auto_follows_codemode(self, runtime_factory):
        runtime, ctx = runtime_factory({"demo": inproc_entry()}, codemode_enabled=True)
        await peer_connect(runtime, "demo", MODERN_PEER)
        registry = ctx.tools
        assert runtime.default_exposure == "codemode"
        assert "mcp__demo__search" not in registry.names(audience="model")


class TestSyncTools:
    """A changed tool list reconciles the registry — newcomers register, the
    gone unregister, the untouched stay where the connect put them."""

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

    async def test_a_failing_direct_server_marks_error_without_raising(self, tmp_path):
        """The runtime's own bounded connect: a server that cannot start is
        reported and remembered on the session, never raised."""
        runtime, ctx = make_runtime(
            tmp_path, {"bad": {"command": "no-such-binary-mocode"}}
        )
        try:
            await asyncio.wait_for(runtime.start(), BOUND)  # must not raise
            status = {s["name"]: s for s in runtime.status()}
            assert status["bad"]["state"] == "error"
            assert status["bad"]["error"]
            assert not [n for n in ctx.tools.names() if n.startswith("mcp__")]
        finally:
            runtime.shutdown()

    async def test_a_silent_server_times_out_the_connect(self, runtime_factory):
        """The runtime's connect timeout bounds a server that never answers:
        the session is marked with an error and the suite keeps moving — the
        same policy a spawned-but-silent child triggers."""
        runtime, ctx = runtime_factory(
            {"slow": {"command": "never-run"}}, connect_timeout_s=1
        )
        session = McpSession(
            runtime.config["slow"],
            server=WirePeer({"server/discover": Silent(), "initialize": Silent()}),
        )
        runtime.sessions["slow"] = session
        try:
            with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                await asyncio.wait_for(session.connect_and_register(), 1)
        finally:
            session.shutdown()
        runtime.shutdown()
        # the runtime's own accounting of the failure, not a private flag
        runtime.tasks.clear()
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
    async def test_the_subscription_lives_and_dies_with_the_connection(
        self, runtime_factory
    ):
        """A change delivered through the subscription reconciles the
        registry; once the connection is closed the same announcement no
        longer moves anything — the watch went down with it."""
        runtime, ctx = runtime_factory({"demo": {"command": "never-run"}})
        env = InProc()
        session = await connect(runtime, "demo", env.server)
        registry = ctx.tools

        env.add("late")
        assert await republish_until(
            env, lambda: "mcp__demo__late" in registry, what="the new tool to register"
        )

        await asyncio.wait_for(session.close(), BOUND)
        assert session.state == STATE_CLOSED
        registry.unregister("mcp__demo__late")
        # after close the cue is still publishable but nothing reconciles: no
        # session answers the change any more
        await env.announce(3)
        assert "mcp__demo__late" not in registry

    async def test_a_sync_shutdown_ends_the_subscription_too(self, runtime_factory):
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

    async def test_a_modern_session_without_a_callback_starts_no_subscription(self):
        env = InProc()
        session = McpSession(inproc_config(), server=env.server)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        # nobody to answer a change: announcing cannot move any registry, so
        # the only observable is that the session stays healthy
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


class TestWatchTools:
    async def test_a_stream_event_drives_the_change_callback(self):
        client = _ScriptedClient([([ToolsListChanged()], "closed")])
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed)
        assert await wait_until(lambda: seen, what="the change callback")
        await _cancel(task)
        # the filter is tools-only: nothing else on the modern vocabularies
        assert client.filters and client.filters[0] == {"tools_list_changed": True}

    async def test_a_dropped_stream_re_listens_after_the_backoff(self):
        client = _ScriptedClient([([], "lost"), ([ToolsListChanged()], "closed")])
        seen: list[int] = []
        reports: list[str] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed, report=reports.append)
        assert await wait_until(
            lambda: len(seen) >= 2, what="the re-listened stream's event"
        )
        await _cancel(task)
        assert client.attempts >= 3  # the drop cost at least one re-listen
        assert client.gaps[0] >= BACKOFF_INITIAL - 0.05  # and it waited first
        assert any("dropped" in message for message in reports)

    async def test_the_backoff_doubles_and_an_event_resets_it(self):
        client = _ScriptedClient(
            [
                ([], "lost"),
                ([ToolsListChanged()], "lost"),
                ([], "lost"),
            ]
        )
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed, report=lambda _message: None)
        assert await wait_until(lambda: client.attempts >= 4, what="four attempts")
        await _cancel(task)
        gaps = client.gaps
        assert gaps[0] >= BACKOFF_INITIAL - 0.05
        assert gaps[1] >= BACKOFF_INITIAL - 0.05  # the event reset the doubling
        assert gaps[2] >= 2 * BACKOFF_INITIAL - 0.05  # before the ceiling

    async def test_a_graceful_close_re_listens(self):
        client = _ScriptedClient([([], "closed"), ([ToolsListChanged()], "closed")])
        seen: list[int] = []

        async def on_changed():
            seen.append(1)

        task = await drive(client, on_changed)
        assert await wait_until(
            lambda: client.attempts >= 2 and seen, what="the re-listen after a close"
        )
        await _cancel(task)
        assert client.gaps[0] >= BACKOFF_INITIAL - 0.05

    async def test_a_refused_subscription_is_raised_never_retried(self):
        from mcp.client.subscriptions import ListenNotSupportedError

        client = _RefusingClient(ListenNotSupportedError("2025-11-25"))
        with pytest.raises(ListenNotSupportedError):
            await asyncio.wait_for(watch_tools(client, _noop), BOUND)
        assert client.calls == 1

    async def test_a_failed_subscription_is_reported_and_raised(self):
        client = _RefusingClient(MCPError(-32601, "the server refuses subscriptions"))
        reports: list[str] = []
        with pytest.raises(MCPError):
            await asyncio.wait_for(
                watch_tools(client, _noop, report=reports.append), BOUND
            )
        assert client.calls == 1
        assert reports  # said before it was raised

    async def test_a_failing_re_sync_is_reported_and_the_stream_continues(self):
        client = _ScriptedClient([([ToolsListChanged(), ToolsListChanged()], "closed")])
        reports: list[str] = []
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

    def test_the_plugin_instance_is_stateless(self):
        assert McpPlugin().name == "mcp"
        assert (
            McpPlugin().description == "Connect to MCP servers and expose their tools"
        )
        assert PLUGIN.name == "mcp"


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
    """
    from mocode.host.plugin.builtin.mcp import runtime as runtime_module

    real = runtime_module.McpSession

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


class TestPromptSection:
    """The mcp_servers prompt section — structure, not wording: which
    servers are listed, how each is reached, and which tool names a
    connected one catalogues."""

    async def test_a_connected_server_joins_the_section(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": make_resource_server()},
            default_exposure="direct",
        )
        registry = ctx.tools
        assert "mcp__demo__search" not in registry  # this server has no tools
        assert "read_mcp_resource" in registry
        section = next(s for s in ctx.prompt_sections if s.name == "mcp_servers")
        assert section.priority == 46
        assert section.derived_from == "tools"
        assert section.pinned is False
        assert ctx.agent.system_prompt.count("<mcp_servers>") == 1

        text = section.render({})
        rows = _status_rows(text)
        assert "demo" in rows
        assert rows["demo"][0] == "direct"
        host.close()

    async def test_connected_servers_list_their_tool_names(
        self, plugin_host, monkeypatch, tmp_path
    ):
        """The D12 catalogue: a connected server lists its tools' raw names
        on an indented continuation line — names only, no schemas."""
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"alpha": WirePeer(MODERN_PEER)}
        )
        section = next(s for s in ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        catalogues, tails = _tool_lines(text)
        assert catalogues["alpha"] == ["ask", "fail", "pic", "search"]
        assert "alpha" not in tails  # no cut, no pointer
        host.close()

    async def test_the_tool_list_truncates_at_thirty_names(
        self, plugin_host, monkeypatch, tmp_path
    ):
        """Past thirty names the list gives up counting and points at
        search_tools() — the truncated raws stay out of the section."""
        tools = [
            {
                "name": f"tool_{i:02d}",
                "description": f"Tool {i:02d}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for i in range(40)
        ]
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"many": WirePeer({**MODERN_PEER, "tools/list": {
                "resultType": "complete", "tools": tools, "ttlMs": 0, "cacheScope": "public"
            }})}
        )
        section = next(s for s in ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        catalogues, tails = _tool_lines(text)
        catalogue = catalogues["many"]
        assert len(catalogue) == 30  # the list holds exactly thirty names
        assert catalogue[0] == "tool_00" and catalogue[-1] == "tool_29"
        assert "tool_30" not in text and "tool_39" not in text
        assert tails["many"].startswith(" … +10 more ")
        host.close()

    async def test_a_still_connecting_server_keeps_the_one_line_form(
        self, plugin_host, monkeypatch, tmp_path
    ):
        """No catalogue without a connection: the failed server keeps the
        plain one-line form and no ``tools:`` line at all."""
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": _plugins_mcp(
                    tmp_path, _table(tmp_path, slow={}), connect_timeout_s=1
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
            await asyncio.wait_for(session.connect_and_register(), 1)
        await asyncio.wait_for(host.materialize(), BOUND)

        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "slow" in _status_rows(text)
        assert "tools:" not in text
        host.close()

    async def test_hidden_and_disabled_servers_stay_out_of_the_section(
        self, plugin_host, monkeypatch, tmp_path
    ):
        peers = {"shown": WirePeer(MODERN_PEER), "hid": WirePeer(MODERN_PEER)}
        _peer_sessions(monkeypatch, peers)
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={
                "plugins": _plugins_mcp(
                    tmp_path,
                    _table(tmp_path, shown={}, hid={"exposure": "hidden"}, off={"enabled": False}),
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

    async def test_no_servers_renders_no_section(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        assert section.render({}) == ""
        assert "<mcp_servers>" not in host.ctx.agent.system_prompt
        host.close()

    async def test_the_section_uses_server_instructions_without_a_description(
        self, plugin_host, monkeypatch, tmp_path
    ):
        """A server with no configured description falls back to the first
        line of the instructions it handed over the wire."""
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)}
        )
        section = next(s for s in ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "Wire server instructions." in text
        host.close()


class TestCodemodeNotice:
    """The one-shot codemode warning is an event on the channel — the
    observable behaviour, not a flag on the runtime."""

    async def test_program_only_tools_warn_once_then_stay_quiet(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)},
            exposure="codemode",
        )
        reader = ctx.subscribe(since=0)
        # the warning travels with the connect; wait for it on the channel
        # rather than sleeping a guessed window and counting after the fact
        warnings = await _first_warning(reader)
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        # the count travels as a leading number — parsed, not spelled out
        assert int(warnings[0].message.split(" ", 1)[0]) == 4
        # one conversation, one warning — a second emission never lands
        assert [e for e in await _drain(reader) if isinstance(e, Notice)] == []
        host.close()

    async def test_codemode_on_suppresses_the_notice(
        self, plugin_host, monkeypatch, tmp_path
    ):
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


def test_resolve_server_exposure():
    assert resolve_server_exposure(_cfg(exposure="hidden"), "direct") == "hidden"
    assert resolve_server_exposure(_cfg(exposure="bogus"), "codemode") == "codemode"
    assert resolve_server_exposure(_cfg(), "deferred") == "deferred"
    assert resolve_server_exposure(_cfg(), "garbage") == "direct"


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

    async def test_program_origin_reaches_codemode_tools_and_the_model_cannot(
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

    async def test_hidden_tools_refuse_every_origin(
        self, plugin_host, monkeypatch, tmp_path
    ):
        host, runtime, ctx = await _materialize_with_servers(
            plugin_host, monkeypatch, tmp_path, {"demo": WirePeer(MODERN_PEER)}, exposure="hidden"
        )
        for origin in ("model", "program"):
            result, _ = await _dispatch(
                host, "mcp__demo__search", {"q": "x"}, origin=origin
            )
            assert result.status == "denied"
        host.close()

    def test_the_package_exports_the_plugin_surface(self):
        from mocode.host.plugin.builtin.mcp import (
            PLUGIN as exported,
            McpPlugin,
            McpRuntime,
        )

        assert isinstance(exported, McpPlugin)
        assert exported.name == "mcp"
        assert McpRuntime is not None


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
            target=self._server.serve_forever, daemon=True
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
                fake.stopped_event.wait(0.5)
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


# ── streamable HTTP ───────────────────────────────────


@pytest.mark.usefixtures("_loopback_only")
class TestStreamableHttp:
    async def test_modern_json_responses_reach_the_session(self, endpoint, http_sessions):
        fake = endpoint("json")
        session = http_sessions(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "http-srv", "version": "1.0"}
        assert session.instructions == "HTTP server instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "mocode"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi mocode"}]
        assert result["structuredContent"] == {"text": "hi mocode"}

    async def test_sse_framed_responses_are_parsed(self, endpoint, http_sessions):
        fake = endpoint("sse")
        session = http_sessions(http_config(fake.url))
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert [t["name"] for t in session.tools] == ["greet"]
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "sse"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        assert result["structuredContent"] == {"text": "hi sse"}

    async def test_configured_headers_reach_the_server(self, endpoint, http_sessions):
        fake = endpoint("json")
        session = http_sessions(
            http_config(
                fake.url,
                headers={"Authorization": "Bearer test-token", "X-Mcp-Test": "yes"},
            )
        )
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.call_tool("greet", {"name": "mocode"}), BOUND)
        posts = [r for r in fake.requests if r["message"] is not None]
        assert posts
        for request in posts:
            assert request["headers"].get("authorization") == "Bearer test-token"
            assert request["headers"].get("x-mcp-test") == "yes"


@pytest.mark.usefixtures("_loopback_only")
class TestLegacyOverStreamableHttp:
    async def test_the_failed_probe_falls_back_to_the_handshake(self, endpoint, http_sessions):
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


# ── the legacy SSE transport ──────────────────────────


@pytest.mark.usefixtures("_loopback_only")
class TestSseTransport:
    async def test_the_handshake_rides_the_stream_and_keeps_the_session(
        self, endpoint, http_sessions
    ):
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
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "sse"}), BOUND)
        assert result["content"] == [{"type": "text", "text": "hi sse"}]
        gets = [r for r in fake.requests if r["message"] is None]
        posts = [r for r in fake.requests if r["message"] is not None]
        assert gets and gets[0]["path"] == "/sse"
        # configured headers reach the GET stream as well as the POSTs
        assert gets[0]["headers"].get("authorization") == "Bearer sse-token"
        # the endpoint event's session id rides every message POST
        assert posts and all("sessionId=sse-session-42" in r["path"] for r in posts)


# ── failures ──────────────────────────────────────────


@pytest.mark.usefixtures("_loopback_only")
class TestConnectionFailures:
    async def test_a_refused_connection_is_a_transport_error(self, http_sessions):
        session = http_sessions(http_config(_refused_url()))
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_transport"
        assert session.state == STATE_ERROR
        assert session.last_error
