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
import time
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
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.shared.subscriptions import ToolsListChanged
from mcp.types import (
    ListResourceTemplatesResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)
import mcp_types as sdk_types

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
from mocode.host.plugin.builtin.mcp.subscriptions import BACKOFF_INITIAL, watch_tools
from mocode.host.plugin.builtin.mcp.tools import (
    RESOURCE_TOOL_NAMES,
    mcp_status_tool,
    mcp_tool,
)
from mocode.host.plugin.context import BuildContext
from mocode.host.plugin.builtin.mcp.runtime import McpRuntime

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


# ── an in-process wire peer — the protocol-shape seam ────────


async def _peer_timer(seconds: float) -> None:
    """How the fake peer waits out a scripted delay.

    A slow server's latency is the behaviour under test here — the same job
    the subprocess fakes' own ``time.sleep`` did — so the wait goes through
    :func:`settle`, the sanctioned entry for exactly that.
    """
    await settle(seconds)


class Unsupported:
    """A ``-32022`` discover refusal naming the versions the peer speaks."""

    def __init__(self, supported: list[str]) -> None:
        self.supported = supported


class Late:
    """A route answered after a delay — the slow-call timeout's shape."""

    def __init__(self, seconds: float, result: dict) -> None:
        self.seconds = seconds
        self.result = result


class Drop:
    """A route that closes the stream instead of answering — a dropped peer."""


class Silent:
    """A route that never answers — a peer that hangs up on nothing."""


class WirePeer:
    """A scripted JSON-RPC server over the SDK's in-memory streams.

    ``routes`` maps a method to what it answers: a ``dict`` result,
    :class:`Late` for a delayed one, :class:`Silent` for no answer at all,
    :class:`Unsupported` for the ``-32022`` era refusal, :class:`Drop` to
    close the stream underneath the client. An unlisted method answers
    ``-32601`` — which is exactly what makes a pre-discover server fall
    back to the handshake. ``seen`` records the methods that arrived, so a
    test asserts on the requests that were really made.
    """

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.seen: list[str] = []

    async def _serve(self, read, write) -> None:
        while True:
            message = await read.receive()
            body = message.message
            if isinstance(body, sdk_types.JSONRPCNotification):
                self.seen.append(body.method)
                continue
            assert isinstance(body, sdk_types.JSONRPCRequest), body
            method, rid = body.method, body.id
            self.seen.append(method)
            out = self.routes.get(method, "unknown")
            if out == "unknown":
                await write.send(
                    SessionMessage(
                        sdk_types.JSONRPCError(
                            jsonrpc="2.0",
                            id=rid,
                            error=sdk_types.ErrorData(
                                code=-32601, message=f"unknown method {method}"
                            ),
                        )
                    )
                )
                continue
            if isinstance(out, Silent):
                continue
            if isinstance(out, ByToolName):
                out = out.answers[body.params.get("name")]
            if isinstance(out, Late):
                # the delay is the behaviour under test (a slow server), so
                # the peer waits on a real timer — and the guard's caller
                # frame is this fake, not the test that drives it.
                await _peer_timer(out.seconds)
                out = out.result
            if isinstance(out, Drop):
                await write.aclose()
                return
            if isinstance(out, Unsupported):
                await write.send(
                    SessionMessage(
                        sdk_types.JSONRPCError(
                            jsonrpc="2.0",
                            id=rid,
                            error=sdk_types.ErrorData(
                                code=-32022,
                                message="unsupported version",
                                data={
                                    "supported": out.supported,
                                    "requested": "2026-07-28",
                                },
                            ),
                        )
                    )
                )
                continue
            await write.send(
                SessionMessage(
                    sdk_types.JSONRPCResponse(jsonrpc="2.0", id=rid, result=out)
                )
            )

    async def __aenter__(self):
        self._cm = create_client_server_memory_streams()
        self._streams = await self._cm.__aenter__()
        self._task = asyncio.ensure_future(self._serve(*self._streams[1]))
        return self._streams[0]

    async def __aexit__(self, *exc) -> bool:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        await self._cm.__aexit__(*exc)
        return False



#: The handshake-era answer for a peer that answers only ``initialize``.
LEGACY_HANDSHAKE = {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "wire-legacy-srv", "version": "1.0"},
    "instructions": "Legacy wire instructions.",
}


class ByToolName:
    """A route that answers by the tool the request names."""

    def __init__(self, answers) -> None:
        self.answers = answers


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
