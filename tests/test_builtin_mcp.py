"""The mcp builtin plugin — configuration, naming, sessions, runtime and the
plugin lifecycle, tested against the official SDK's client.

Two seams, one per job. The protocol shape (era negotiation, wire-form
results, error mapping) is exercised through an **in-process
``mcp.server.MCPServer``** — the SDK's own test shape — connected to through
``McpSession(server=...)``. The stdio transport (spawn, environment, stderr
tail, child teardown, reconnect) is exercised through **subprocess fake
servers**: Python scripts written into ``tmp_path`` and launched with
``sys.executable`` — no shell. Those scripts write their own pid into a
pidfile whose path arrives in the environment, so a test can watch the
direct child it spawned; POSIX-only process semantics branch on
``sys.platform``. Every async path is bounded by a timeout so a broken fake
server can never hang the suite on Windows.

Note for the fakes: at protocol 2026-07-28 the SDK requires ``ttlMs`` and
``cacheScope`` in a ``tools/list`` result, so the modern fakes carry them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
    tool_full_name,
)


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


# ── fake servers: in-process and subprocess ─────────────────

import asyncio
import json
import os
import textwrap
import time

import pytest
import pytest_asyncio
from mcp.server import MCPServer

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

MODERN_SERVER = r'''
import json, sys, os, time

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

pidfile = os.environ.get("MCP_TEST_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))
sys.stderr.write("modern server starting\n")
sys.stderr.flush()

PAGE_1 = [
    {"name": "search", "description": "Search things",
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "fail", "description": "Always fails",
     "inputSchema": {"type": "object", "properties": {}}},
]
PAGE_2 = [
    {"name": "ask", "description": "Needs user input",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pic", "description": "Returns an image",
     "inputSchema": {"type": "object", "properties": {}}},
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") or {}
    if method == "server/discover":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25"],
            "capabilities": {"tools": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "modern-srv", "version": "2.0"}},
            "instructions": "Modern server instructions."}})
    elif method == "tools/list":
        if "_meta" not in params:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "missing _meta"}})
        elif params.get("cursor") == "page-2":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": PAGE_2, "ttlMs": 0, "cacheScope": "public"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": PAGE_1, "nextCursor": "page-2", "ttlMs": 0, "cacheScope": "public"}})
    elif method == "tools/call":
        name = params.get("name")
        if "_meta" not in params:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "missing _meta"}})
        elif name == "fail":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "boom"}], "isError": True}})
        elif name == "ask":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "input_required",
                "inputRequests": {"login": {"method": "elicitation/create", "params": {
                    "mode": "form", "message": "log in",
                    "requestedSchema": {"type": "object", "properties": {}}}}},
                "requestState": "opaque"}})
        elif name == "sleep":
            time.sleep(3)
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "woke up"}]}})
        elif name == "pic":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [
                {"type": "text", "text": "here:"},
                {"type": "image", "data": "QUJD", "mimeType": "image/png"}],
                "structuredContent": {"n": 1}}})
        elif name == "env":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text",
                "text": json.dumps({"MARKER": os.environ.get("MARKER"),
                                    "ENTRY_K": os.environ.get("ENTRY_K"),
                                    "PLUGIN_ROOT": os.environ.get("PLUGIN_ROOT"),
                                    "PATH_SET": "PATH" in os.environ})}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "hello"}], "structuredContent": {"ok": True}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

LEGACY_SERVER = r'''
import json, sys, os

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

pidfile = os.environ.get("MCP_TEST_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))
sys.stderr.write("legacy server starting\n")
sys.stderr.flush()

TOOLS = [
    {"name": "echo", "description": "Echo arguments",
     "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}},
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") or {}
    if method == "notifications/initialized":
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "legacy-srv", "version": "1.0"},
            "instructions": "Legacy server instructions."}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = params.get("name")
        if name == "add_tool":
            TOOLS.append({"name": "late", "description": "Arrived later",
                          "inputSchema": {"type": "object", "properties": {}}})
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "added"}]}})
        elif name == "remove_tool":
            TOOLS[:] = [t for t in TOOLS if t["name"] != "late"]
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "removed"}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "echo:" + str(params.get("arguments"))}], "structuredContent": {"legacy": True}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

HYBRID_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    if method == "server/discover":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "unsupported version", "data": {"supported": ["2025-11-25"], "requested": "2026-07-28"}}})
    elif method == "notifications/initialized":
        continue
    elif method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hybrid-srv", "version": "1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": []}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

DISJOINT_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), rid = req.get("id")
    if method == "server/discover":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "unsupported version", "data": {"supported": ["2026-08-30"], "requested": "2026-07-28"}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

SILENT_SERVER = r'''
import sys
for line in sys.stdin:
    pass
'''

MANY_SERVER = r'''
import json, sys, os

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

pidfile = os.environ.get("MCP_TEST_PIDFILE")
if pidfile:
    open(pidfile, "w").write(str(os.getpid()))

TOOLS = [
    {"name": "tool_%02d" % i, "description": "Tool %02d" % i,
     "inputSchema": {"type": "object", "properties": {}}}
    for i in range(40)
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method, rid = req.get("method"), req.get("id")
    if method == "server/discover":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "resultType": "complete",
            "supportedVersions": ["2026-07-28", "2025-11-25"],
            "capabilities": {"tools": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "many-srv", "version": "2.0"}},
            "instructions": "Many server instructions."}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": TOOLS, "ttlMs": 0, "cacheScope": "public"}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "ok"}], "structuredContent": {"ok": True}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

BOUND = 15  # seconds — every session operation in this file stays bounded


def write_server(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


def _child_pidfile(tmp_path: Path, name: str) -> Path:
    """Where the *name* fake server writes its own pid."""
    return tmp_path / f"{name}.pid"


def server_config(
    script: Path,
    *,
    name: str = "demo",
    timeout: float = 30.0,
    pidfile: Path | None = None,
    **kwargs,
) -> McpServerConfig:
    """A stdio config for a fake *script*: its pidfile (so a test can watch
    the direct child) plus any environment overlay the test needs."""
    kwargs.setdefault("source", "test")
    env = dict(kwargs.pop("env", {}))
    if pidfile is not None:
        env.setdefault("MCP_TEST_PIDFILE", str(pidfile))
    return McpServerConfig(
        name=name,
        command=sys.executable,
        args=[str(script)],
        timeout=timeout,
        env=env,
        **kwargs,
    )


def _read_pid(pidfile: Path) -> int:
    """The fake server's own pid, once it has written it."""
    for _ in range(200):
        if pidfile.exists():
            return int(pidfile.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"{pidfile} never appeared")


def child_alive(pid: int) -> bool:
    """Whether the direct child *pid* is still running.

    The SDK spawns the child inside a Job Object on Windows, which closes
    the ``PROCESS_ALL_ACCESS`` handle ``os.kill(pid, 0)`` asks for — so the
    probe there is a ``SYNCHRONIZE`` handle instead. POSIX: signal 0.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def wait_gone(pid: int, attempts: int = 200) -> bool:
    """Wait (bounded) for a direct child to be reaped."""
    for _ in range(attempts):
        if not child_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


# ── an in-process server — the protocol-shape seam ───────────


def _greet(name: str) -> str:
    return f"hi {name}"


def _plain() -> str:
    return "plain"


def make_server(
    *,
    name: str = "inproc",
    version: str = "9.9.9",
    instructions: str = "In-proc instructions.",
) -> MCPServer:
    """An in-process MCP server — the SDK's own test shape: a modern server
    with two tools, one of them with a schema."""
    server = MCPServer(name=name, version=version, instructions=instructions)
    server.add_tool(_greet, name="greet", description="Say hi")
    server.add_tool(_plain, name="plain")
    return server


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """Build McpSessions — over an in-process server (protocol shape) or a
    fake script (stdio integration); close them all on teardown."""
    created: list[McpSession] = []

    def factory(
        code: str | None = None,
        *,
        name: str = "demo",
        timeout: float = 30.0,
        server: MCPServer | None = None,
        **kwargs,
    ) -> McpSession:
        if server is None:
            assert code is not None, "a stdio session needs a fake server script"
            script = write_server(tmp_path, f"server_{len(created)}.py", code)
            cfg = server_config(
                script, name=name, timeout=timeout, pidfile=_child_pidfile(tmp_path, name)
            )
        else:
            cfg = server_config(Path("unused"), name=name, timeout=timeout)
        session = McpSession(cfg, server=server, **kwargs)
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
        session = session_factory(server=make_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "inproc", "version": "9.9.9"}
        assert session.instructions == "In-proc instructions."
        assert session.state == STATE_CONNECTED
        assert session.last_error is None
        assert session.server_capabilities is not None
        assert session.server_capabilities.tools is not None

    async def test_connect_hands_the_runtime_the_wire_form_tools(self, session_factory):
        connected: list[list[dict]] = []

        async def on_connected(session, tools):
            connected.append(tools)

        session = session_factory(server=make_server(), on_connected=on_connected)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        tools = session.tools
        assert [t["name"] for t in tools] == ["greet", "plain"]
        assert len(connected) == 1 and connected[0] == tools
        by_name = {t["name"]: t for t in tools}
        assert by_name["greet"]["description"] == "Say hi"
        schema = by_name["greet"]["inputSchema"]
        assert schema["type"] == "object"
        assert "name" in schema["properties"]  # a JSON Schema object node
        # a tool with no description reads back as the empty string
        assert by_name["plain"]["description"] == ""

    async def test_call_results_come_back_as_wire_form_dicts(self, session_factory):
        session = session_factory(server=make_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("greet", {"name": "mocode"}), BOUND)
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "hi mocode"}]
        assert result["structuredContent"] == {"result": "hi mocode"}

    async def test_a_slow_call_times_out(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_slow.py", MODERN_SERVER),
            name="slow",
            timeout=0.4,
            pidfile=_child_pidfile(tmp_path, "slow"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("sleep"), BOUND)
        assert err.value.code == "mcp_timeout"
        assert session.last_error and "timed out" in session.last_error
        await asyncio.wait_for(session.close(), BOUND)

    async def test_input_required_raises_its_own_error(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_ask.py", MODERN_SERVER),
            name="ask",
            pidfile=_child_pidfile(tmp_path, "ask"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("ask"), BOUND)
        assert err.value.code == "mcp_input_required"
        assert "elicitation is not supported" in str(err.value)
        await asyncio.wait_for(session.close(), BOUND)

    async def test_non_text_content_blocks_pass_through(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_pic.py", MODERN_SERVER),
            name="pic",
            pidfile=_child_pidfile(tmp_path, "pic"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("pic"), BOUND)
        block = result["content"][1]
        assert block["type"] == "image"
        assert block["mimeType"] == "image/png"
        assert block["data"] == "QUJD"
        assert result["structuredContent"] == {"n": 1}
        await asyncio.wait_for(session.close(), BOUND)

    async def test_the_environment_reaches_the_child(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_env.py", MODERN_SERVER),
            name="env",
            pidfile=_child_pidfile(tmp_path, "env"),
            env={"MARKER": "from-parent", "ENTRY_K": "from-entry"},
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("env"), BOUND)
        seen = json.loads(result["content"][0]["text"])
        # the whole process environment plus the entry overlay — the SDK
        # layers its env over a trimmed platform default, so both must be
        # passed explicitly (decision D8)
        assert seen == {
            "MARKER": "from-parent",
            "ENTRY_K": "from-entry",
            "PLUGIN_ROOT": None,
            "PATH_SET": True,
        }
        await asyncio.wait_for(session.close(), BOUND)

    async def test_stderr_is_collected_into_a_tail(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_err.py", MODERN_SERVER),
            name="err",
            pidfile=_child_pidfile(tmp_path, "err"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.list_tools(), BOUND)
        assert "modern server starting" in session.stderr_tail
        await asyncio.wait_for(session.close(), BOUND)

    async def test_close_terminates_the_child(self, session_factory, tmp_path):
        pidfile = _child_pidfile(tmp_path, "close")
        cfg = server_config(
            write_server(tmp_path, "modern_close.py", MODERN_SERVER),
            name="close",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        pid = _read_pid(pidfile)
        assert child_alive(pid)
        await asyncio.wait_for(session.close(), BOUND)
        assert await wait_gone(pid)
        assert session.state == STATE_CLOSED

    async def test_shutdown_terminates_the_child(self, session_factory, tmp_path):
        pidfile = _child_pidfile(tmp_path, "kill")
        cfg = server_config(
            write_server(tmp_path, "modern_kill.py", MODERN_SERVER),
            name="kill",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        pid = _read_pid(pidfile)
        session.shutdown()  # sync, non-blocking — the unwind is scheduled
        assert await wait_gone(pid)
        assert session.state == STATE_CLOSED

    async def test_a_dropped_server_disconnects_then_reconnects(self, session_factory, tmp_path):
        pidfile = _child_pidfile(tmp_path, "drop")
        cfg = server_config(
            write_server(tmp_path, "modern_drop.py", MODERN_SERVER),
            name="drop",
            pidfile=pidfile,
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        pid = _read_pid(pidfile)
        # a request in flight when the child dies must fail, not hang
        task = asyncio.create_task(session.call_tool("sleep"))
        await asyncio.sleep(0.3)
        if sys.platform == "win32":
            os.kill(pid, 9)
        else:
            os.kill(pid, 15)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(task, BOUND)
        assert err.value.code == "mcp_transport"
        assert "disconnected" in str(err.value)
        assert session.state == STATE_DISCONNECTED
        # the SDK's client cannot be re-entered: the next call reconnects
        result = await asyncio.wait_for(session.call_tool("search", {"q": "x"}), BOUND)
        assert result["content"][0]["text"] == "hello"
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        await asyncio.wait_for(session.close(), BOUND)

    async def test_a_call_after_close_is_refused(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "modern_after.py", MODERN_SERVER),
            name="after",
            pidfile=_child_pidfile(tmp_path, "after"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.close(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("search", {"q": "x"}), BOUND)
        assert err.value.code == "mcp_closed"

    async def test_an_unstartable_command_is_an_error(self, tmp_path):
        cfg = server_config(
            tmp_path / "missing.py",
            name="broken",
            pidfile=_child_pidfile(tmp_path, "broken"),
        )
        cfg.command = "definitely-not-a-real-binary-mocode"
        session = McpSession(cfg)
        try:
            with pytest.raises(McpError) as err:
                await asyncio.wait_for(session.connect_and_register(), BOUND)
            assert "failed to start" in str(err.value)
            assert session.state == STATE_ERROR
            assert session.last_error
        finally:
            session.shutdown()


class TestLegacySession:
    async def test_the_handshake_negotiates_the_legacy_era(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "legacy_connect.py", LEGACY_SERVER),
            name="legacy",
            pidfile=_child_pidfile(tmp_path, "legacy"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy server instructions."
        assert session.state == STATE_CONNECTED
        assert [t["name"] for t in session.tools] == ["echo"]
        await asyncio.wait_for(session.close(), BOUND)

    async def test_calls_come_back_as_wire_form_dicts(self, session_factory, tmp_path):
        """The handshake era speaks the same wire form; a legacy server that
        echoed arguments comes back as text and structuredContent."""
        cfg = server_config(
            write_server(tmp_path, "legacy_call.py", LEGACY_SERVER),
            name="legacy2",
            pidfile=_child_pidfile(tmp_path, "legacy2"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        result = await asyncio.wait_for(session.call_tool("echo", {"x": "1"}), BOUND)
        assert result["content"][0]["text"] == "echo:{'x': '1'}"
        assert result["structuredContent"] == {"legacy": True}
        assert result["isError"] is False
        await asyncio.wait_for(session.close(), BOUND)

    async def test_the_list_changed_notification_fires_the_callback(self, session_factory, tmp_path):
        seen: list[str] = []

        async def on_tools_changed(session: McpSession) -> None:
            tools = await session.list_tools()
            seen.extend(t["name"] for t in tools)

        cfg = server_config(
            write_server(tmp_path, "legacy_notify.py", LEGACY_SERVER),
            name="legacy3",
            pidfile=_child_pidfile(tmp_path, "legacy3"),
        )
        session = McpSession(cfg, on_tools_changed=on_tools_changed)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        await asyncio.wait_for(session.call_tool("add_tool"), BOUND)
        for _ in range(200):
            if "late" in seen:
                break
            await asyncio.sleep(0.05)
        assert "late" in seen
        await asyncio.wait_for(session.close(), BOUND)


class TestEraNegotiation:
    async def test_a_modern_server_negotiates_the_current_version(self, session_factory):
        session = session_factory(server=make_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"

    async def test_a_discover_failure_falls_back_to_the_handshake(self, session_factory, tmp_path):
        """A server that does not answer the modern probe is legacy — the
        standard forbids deciding the era on a single error code."""
        cfg = server_config(
            write_server(tmp_path, "neg_legacy.py", LEGACY_SERVER),
            name="neg1",
            pidfile=_child_pidfile(tmp_path, "neg1"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        await asyncio.wait_for(session.close(), BOUND)

    async def test_an_unsupported_version_error_falls_back_to_the_handshake(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "neg_hybrid.py", HYBRID_SERVER),
            name="neg2",
            pidfile=_child_pidfile(tmp_path, "neg2"),
        )
        session = McpSession(cfg)
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "hybrid-srv", "version": "1.0"}
        await asyncio.wait_for(session.close(), BOUND)

    async def test_a_server_sharing_no_version_fails_the_connection(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "neg_disjoint.py", DISJOINT_SERVER),
            name="neg3",
            pidfile=_child_pidfile(tmp_path, "neg3"),
        )
        session = McpSession(cfg)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert err.value.code == "mcp_error"
        assert session.state == STATE_ERROR
        assert session.last_error
        await asyncio.wait_for(session.close(), BOUND)

    async def test_a_silent_server_fails_the_connect_under_a_bound(self, session_factory, tmp_path):
        cfg = server_config(
            write_server(tmp_path, "neg_silent.py", SILENT_SERVER),
            name="neg4",
            pidfile=_child_pidfile(tmp_path, "neg4"),
        )
        session = McpSession(cfg)
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(session.connect_and_register(), 2)
        assert session.state != STATE_CONNECTED
        await asyncio.wait_for(session.close(), BOUND)


def make_resource_server() -> MCPServer:
    """An in-process server with one concrete resource and one template —
    the shape the resource tools read through."""
    server = MCPServer(name="withres", version="1.2.3")

    @server.resource("note://today")
    def today() -> str:
        "today's note"

        return "ship it"

    @server.resource("greeting://{name}")
    def greeting(name: str) -> str:
        "a greeting"

        return f"hello {name}"

    return server


class TestResourceMethods:
    """The session's resource pass-throughs, wire-form — the tools built on
    them (a later wave) only split contents and map errors."""

    async def test_listing_resources_and_templates_is_wire_form(self, session_factory):
        session = session_factory(server=make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        assert session.server_capabilities.resources is not None

        resources = await asyncio.wait_for(session.list_resources(), BOUND)
        entries = resources["resources"]
        assert [r["uri"] for r in entries] == ["note://today"]
        assert entries[0]["name"] == "today"
        assert entries[0]["mimeType"] == "text/plain"

        templates = await asyncio.wait_for(session.list_resource_templates(), BOUND)
        assert [t["uriTemplate"] for t in templates["resourceTemplates"]] == [
            "greeting://{name}"
        ]

    async def test_reading_a_resource_returns_its_contents(self, session_factory):
        session = session_factory(server=make_resource_server())
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
        session = session_factory(server=make_resource_server())
        await asyncio.wait_for(session.connect_and_register(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.read_resource("note://absent"), BOUND)
        assert err.value.code == "mcp_error"


# ── runtime + tool registration ─────────────────────────────

from mocode.core.events import Notice
from mocode.core.tool import ToolError
from mocode.host.config import Config
from mocode.host.plugin.builtin.mcp.runtime import McpRuntime
from mocode.host.plugin.builtin.mcp.tools import mcp_status_tool, mcp_tool
from mocode.host.plugin.context import BuildContext


def make_runtime(
    tmp_path: Path,
    servers: dict,
    *,
    mcp_extra: dict | None = None,
    codemode_enabled: bool = False,
) -> McpRuntime:
    plugins = {"mcp": {"servers": servers, **(mcp_extra or {})}}
    if codemode_enabled:
        plugins["codemode"] = {"enabled": True}
    config = Config(provider="p", model="m", plugins=plugins)
    ctx = BuildContext(home=tmp_path / "home", cwd=tmp_path, config=config)
    return McpRuntime(ctx)


def script_entry(script: Path, **extra) -> dict:
    return {"command": sys.executable, "args": [str(script)], **extra}


@pytest_asyncio.fixture
async def runtime_factory(tmp_path):
    """Build runtimes over fake servers; shut them down on teardown."""
    created: list[McpRuntime] = []

    def factory(servers: dict, *, mcp_extra: dict | None = None,
                codemode_enabled: bool = False) -> McpRuntime:
        runtime = make_runtime(
            tmp_path, servers, mcp_extra=mcp_extra, codemode_enabled=codemode_enabled
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


class TestToolMapping:
    async def test_tools_register_with_full_names_and_schemas(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_modern.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)

        registry = runtime._ctx.tools
        assert "mcp__demo__search" in registry
        tool = registry.get("mcp__demo__search")
        assert tool.availability == "both"  # default exposure: auto → direct (no codemode)
        assert tool.schema["required"] == ["q"]
        assert tool.tags == frozenset({"mcp", "mcp:demo"})
        assert tool.mcp == {"server": "demo", "tool": "search"}
        assert tool.mcp_raw_name == "search"
        assert tool.source == "host"  # a bare BuildContext stamps nothing

    async def test_a_successful_call_maps_into_content_and_details(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_call.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)
        tool = runtime._ctx.tools.get("mcp__demo__search")

        result = await asyncio.wait_for(tool.run_async({"q": "hi"}), BOUND)
        assert result.content == "hello"
        assert result.details["server"] == "demo"
        assert result.details["tool"] == "search"
        assert result.details["structured_content"] == {"ok": True}
        assert result.details["is_error"] is False

    async def test_an_is_error_result_raises_mcp_error(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_fail.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)
        tool = runtime._ctx.tools.get("mcp__demo__fail")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "mcp_error"
        assert "boom" in err.value.message

    async def test_input_required_raises_its_own_code(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_ask.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)
        tool = runtime._ctx.tools.get("mcp__demo__ask")
        with pytest.raises(ToolError) as err:
            await asyncio.wait_for(tool.run_async({}), BOUND)
        assert err.value.code == "mcp_input_required"

    async def test_image_blocks_land_in_details_with_a_placeholder(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_pic.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)
        tool = runtime._ctx.tools.get("mcp__demo__pic")
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        assert result.content == "here:\n[image: image/png]"
        assert result.details["images"][0]["data"] == "QUJD"

    async def test_missing_schema_and_description_fall_back(self, runtime_factory, tmp_path):
        runtime = runtime_factory({})
        session = McpSession(server_config(tmp_path / "x.py"))
        tool = mcp_tool(runtime, session, "demo", {"name": "raw"}, "both", False)
        assert tool.schema == {"type": "object", "properties": {}}
        assert tool.description == "MCP tool raw from demo"

    async def test_collision_gets_the_hash_suffix_and_raw_name(self, runtime_factory, tmp_path):
        import hashlib

        runtime = runtime_factory({})
        session = McpSession(server_config(tmp_path / "x.py"))
        runtime.assignments["demo"] = assign_tool_names("demo", ["a-b", "a_b"])
        raw = {"name": "a_b"}
        tool = mcp_tool(runtime, session, "demo", raw, "both", False)
        assert tool.name == "mcp__demo__a_b_" + hashlib.sha1(b"a_b").hexdigest()[:6]
        assert tool.mcp_raw_name == "a_b"

    def test_mcp_status_tool_holds_the_runtime(self, runtime_factory):
        runtime = runtime_factory({"off": script_entry("whatever", enabled=False)})
        tool = mcp_status_tool(runtime)
        assert tool.name == "mcp_status"
        assert tool.availability == "program"
        assert tool.mcp_runtime is runtime
        assert tool.schema == {"type": "object", "properties": {}}

    async def test_mcp_status_reports_every_server(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_status.py", MODERN_SERVER)
        runtime = runtime_factory(
            {
                "demo": script_entry(script),
                "off": script_entry(script, enabled=False),
                "broken": script_entry(script, command="no-such-binary-mocode"),
            }
        )
        await asyncio.wait_for(runtime.start(), BOUND)
        tool = mcp_status_tool(runtime)
        result = await asyncio.wait_for(tool.run_async({}), BOUND)
        servers = {s["name"]: s for s in result.details["servers"]}
        assert servers["demo"]["state"] == "connected"
        assert servers["demo"]["tools"] == 4
        assert servers["off"]["state"] == "disabled"
        assert servers["broken"]["state"] == "error"
        assert servers["broken"]["error"]
        assert "demo: connected (4 tools)" in result.content


class TestExposureMapping:
    async def test_codemode_exposure_is_program_only(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_exp_cm.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script, exposure="codemode")})
        await asyncio.wait_for(runtime.start(), BOUND)
        registry = runtime._ctx.tools
        assert "mcp__demo__search" in registry
        assert "mcp__demo__search" not in registry.names(audience="model")
        assert "mcp__demo__search" in registry.names(audience="program")

    async def test_hidden_exposure_registers_then_disables(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_exp_hd.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script, exposure="hidden")})
        await asyncio.wait_for(runtime.start(), BOUND)
        registry = runtime._ctx.tools
        assert registry.get("mcp__demo__search") is not None  # registered
        assert "mcp__demo__search" not in registry.names(audience="model")
        assert "mcp__demo__search" not in registry.names(audience="program")

    async def test_tool_exposure_overrides_per_tool(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_exp_te.py", MODERN_SERVER)
        runtime = runtime_factory(
            {
                "demo": script_entry(
                    script,
                    exposure="codemode",
                    toolExposure={"search": "direct", "pic": "hidden"},
                )
            }
        )
        await asyncio.wait_for(runtime.start(), BOUND)
        registry = runtime._ctx.tools
        assert "mcp__demo__search" in registry.names(audience="model")
        assert "mcp__demo__ask" not in registry.names(audience="model")
        assert registry.get("mcp__demo__pic") is not None
        assert "mcp__demo__pic" not in registry.names(audience="model")

    async def test_default_exposure_auto_follows_codemode(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_exp_auto.py", MODERN_SERVER)
        runtime = runtime_factory(
            {"demo": script_entry(script)}, codemode_enabled=True
        )
        await asyncio.wait_for(runtime.start(), BOUND)
        registry = runtime._ctx.tools
        assert runtime.default_exposure == "codemode"
        assert "mcp__demo__search" not in registry.names(audience="model")


class TestSyncTools:
    async def test_list_changed_adds_and_removes_tools(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_sync.py", LEGACY_SERVER)
        runtime = runtime_factory({"demo": script_entry(script)})
        await asyncio.wait_for(runtime.start(), BOUND)
        registry = runtime._ctx.tools
        assert "mcp__demo__late" not in registry

        session = runtime.sessions["demo"]
        await asyncio.wait_for(session.call_tool("add_tool"), BOUND)
        await asyncio.sleep(0.3)
        assert "mcp__demo__late" in registry

        await asyncio.wait_for(session.call_tool("remove_tool"), BOUND)
        await asyncio.sleep(0.3)
        assert "mcp__demo__late" not in registry

    async def test_a_failing_direct_server_marks_error_without_raising(self, runtime_factory, tmp_path):
        runtime = runtime_factory({"bad": script_entry("x", command="no-such-binary-mocode")})
        await asyncio.wait_for(runtime.start(), BOUND)  # must not raise
        status = {s["name"]: s for s in runtime.status()}
        assert status["bad"]["state"] == "error"
        assert status["bad"]["error"]

    async def test_a_silent_server_times_out_the_connect(self, runtime_factory, tmp_path):
        """The runtime's connect timeout bounds a server that never answers:
        the session is marked with an error, the suite keeps moving."""
        script = write_server(tmp_path, "rt_silent.py", SILENT_SERVER)
        runtime = runtime_factory(
            {"slow": script_entry(script)}, mcp_extra={"connect_timeout_s": 1}
        )
        await asyncio.wait_for(runtime.start(), BOUND)
        status = {s["name"]: s for s in runtime.status()}
        assert status["slow"]["state"] == "error"
        assert "timed out" in status["slow"]["error"]
        assert status["slow"]["tools"] == 0

    async def test_codemode_disabled_marks_warning_sent(self, runtime_factory, tmp_path):
        """With program-only tools and codemode off, the one-shot warning is
        marked (a bare context has no agent to emit to — T6 covers the real
        Notice through a conversation)."""
        script = write_server(tmp_path, "rt_warn.py", MODERN_SERVER)
        runtime = runtime_factory({"demo": script_entry(script, exposure="codemode")})
        await asyncio.wait_for(runtime.start(), BOUND)
        assert runtime._codemode_warned is True

    async def test_codemode_enabled_suppresses_the_warning(self, runtime_factory, tmp_path):
        script = write_server(tmp_path, "rt_warn2.py", MODERN_SERVER)
        runtime = runtime_factory(
            {"demo": script_entry(script, exposure="codemode")},
            codemode_enabled=True,
        )
        await asyncio.wait_for(runtime.start(), BOUND)
        assert runtime._codemode_warned is False


# ── plugin lifecycle ────────────────────────────────────────

from mocode.host.plugin.builtin.mcp import PLUGIN, McpPlugin, McpRuntime
from mocode.host.plugin.builtin.mcp.naming import resolve_server_exposure


def _demo_servers(tmp_path: Path, script: Path, **servers) -> dict:
    """mcpServers table for <cwd>/.mocode/mcp.json — each server's env names
    its pidfile, so a test can watch the direct child the plugin spawned."""
    table = {}
    for name, extra in servers.items():
        env = {"MCP_TEST_PIDFILE": str(_child_pidfile(tmp_path, name))}
        env.update(extra.pop("env", {}))
        table[name] = {"command": sys.executable, "args": [str(script)], "env": env, **extra}
    return table


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
        assert McpPlugin().description == "Connect to MCP servers and expose their tools"
        assert PLUGIN.name == "mcp"

    async def test_prepare_connects_and_the_section_joins_the_prompt(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "pl_modern.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script, demo={"description": "Search things"}
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)

        registry = host.ctx.tools
        assert "mcp__demo__search" in registry.names(audience="model")
        assert host.ctx.agent.system_prompt.count("<mcp_servers>") == 1
        assert "- demo: direct — Search things" in host.ctx.agent.system_prompt

        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        assert section.priority == 46
        assert section.derived_from == "tools"
        assert section.pinned is False

        host.close()
        runtime = registry.get("mcp_status").mcp_runtime
        pid = _read_pid(_child_pidfile(tmp_path, "demo"))
        assert await wait_gone(pid)  # close() killed the child
        assert runtime.sessions["demo"].state == STATE_CLOSED

    async def test_the_section_uses_server_instructions_without_a_description(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "pl_instr.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": _demo_servers(tmp_path, script, demo={})},
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        assert "- demo: direct — Modern server instructions." in (
            host.ctx.agent.system_prompt
        )
        host.close()

    async def test_hidden_and_disabled_servers_stay_out_of_the_section(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "pl_hidden.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path,
                    script,
                    shown={},
                    hid={"exposure": "hidden"},
                    off={"enabled": False},
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "- shown: direct" in text
        assert "hid" not in text
        assert "off" not in text
        # hidden still registered — just unreachable
        assert host.ctx.tools.get("mcp__hid__search") is not None
        host.close()

    async def test_connected_servers_list_their_tool_names(
        self, plugin_host, tmp_path
    ):
        """The D12 catalogue: a connected server lists its tools' raw names
        on an indented continuation line — names only, no schemas."""
        modern = write_server(tmp_path, "pl_list_modern.py", MODERN_SERVER)
        legacy = write_server(tmp_path, "pl_list_legacy.py", LEGACY_SERVER)
        servers = {
            **_demo_servers(tmp_path, modern, alpha={}),
            **_demo_servers(tmp_path, legacy, beta={}),
        }
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"mcpServers": servers})
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "- alpha: direct" in text
        assert "  tools: ask, fail, pic, search" in text
        assert "- beta: direct" in text
        assert "  tools: echo" in text
        host.close()

    async def test_the_tool_list_truncates_at_thirty_names(
        self, plugin_host, tmp_path
    ):
        """Past thirty names the list gives up counting and points at
        search_tools() — the truncated raws stay out of the section."""
        script = write_server(tmp_path, "pl_many.py", MANY_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": _demo_servers(tmp_path, script, many={})},
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        head = ", ".join(f"tool_{i:02d}" for i in range(30))
        assert f"  tools: {head} … +10 more (search_tools() in a codemode script)" in text
        assert "tool_30" not in text
        assert "tool_39" not in text
        host.close()

    async def test_a_server_still_connecting_keeps_the_one_line_form(
        self, plugin_host, tmp_path
    ):
        """No catalogue without a connection: the timed-out server keeps the
        plain `- name: how` line and no `tools:` line at all."""
        script = write_server(tmp_path, "pl_slow.py", SILENT_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": _demo_servers(tmp_path, script, slow={})},
        )
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={"plugins": {"mcp": {"connect_timeout_s": 1}}},
        )
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "- slow: direct" in text
        assert "tools:" not in text
        host.close()

    async def test_a_hidden_server_leaves_no_trace_in_the_section(
        self, plugin_host, tmp_path
    ):
        """Hidden skips the whole row (decision D12) — neither the server
        line nor its registered tools' names may appear."""
        shown = write_server(tmp_path, "pl_shown.py", MODERN_SERVER)
        buried = write_server(tmp_path, "pl_buried.py", MANY_SERVER)
        servers = {
            **_demo_servers(tmp_path, shown, shown={}),
            **_demo_servers(tmp_path, buried, buried={"exposure": "hidden"}),
        }
        write_mcp_json(tmp_path / ".mocode" / "mcp.json", {"mcpServers": servers})
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "  tools: ask, fail, pic, search" in text
        assert "buried" not in text
        assert "tool_00" not in text
        # hidden still registered — just unreachable
        assert host.ctx.tools.get("mcp__buried__tool_00") is not None
        host.close()

    async def test_a_per_tool_hidden_entry_stays_out_of_the_list(
        self, plugin_host, tmp_path
    ):
        """``toolExposure: hidden`` registers the tool and disables it — the
        catalogue lists callable names only, so the hidden raw name stays
        out while its server remains listed."""
        script = write_server(tmp_path, "pl_pthidden.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path,
                    script,
                    demo={"toolExposure": {"fail": "hidden"}},
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        registry = host.ctx.tools
        assert registry.get("mcp__demo__fail") is not None  # registered
        assert "mcp__demo__fail" not in registry.names(audience="program")
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        text = section.render({})
        assert "  tools: ask, pic, search" in text
        assert "fail" not in text
        host.close()

    async def test_no_servers_renders_no_section(self, plugin_host):
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        section = next(s for s in host.ctx.prompt_sections if s.name == "mcp_servers")
        assert section.render({}) == ""
        assert "<mcp_servers>" not in host.ctx.agent.system_prompt
        host.close()

    async def test_codemode_warning_notice_emits_once_per_conversation(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "pl_warn.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script, demo={"exposure": "codemode"}
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        reader = host.ctx.subscribe(since=0)
        await asyncio.wait_for(host.materialize(), BOUND)
        runtime = host.ctx.tools.get("mcp_status").mcp_runtime
        for _ in range(200):
            if runtime._codemode_warned:
                break
            await asyncio.sleep(0.05)
        assert runtime._codemode_warned
        # let a possible second emission land, then count
        await asyncio.sleep(0.5)
        seen = []
        while (event := reader.take()) is not None:
            seen.append(event)
        warnings = [
            e
            for e in seen
            if isinstance(e, Notice) and "reachable only through codemode" in e.message
        ]
        assert len(warnings) == 1
        assert warnings[0].level == "warn"
        assert warnings[0].message.startswith("4 MCP tools")
        host.close()

    async def test_codemode_enabled_suppresses_the_notice(self, plugin_host, tmp_path):
        script = write_server(tmp_path, "pl_cm.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script, demo={"exposure": "codemode"}
                )
            },
        )
        host = plugin_host(
            plugins=[PLUGIN],
            build=True,
            assemble=True,
            config_kwargs={"plugins": {"codemode": {"enabled": True}}},
        )
        reader = host.ctx.subscribe(since=0)
        await asyncio.wait_for(host.materialize(), BOUND)
        runtime = host.ctx.tools.get("mcp_status").mcp_runtime
        await asyncio.sleep(0.5)
        assert runtime._codemode_warned is False
        seen = []
        while (event := reader.take()) is not None:
            seen.append(event)
        assert not [e for e in seen if isinstance(e, Notice)]
        host.close()

    def test_resolve_server_exposure(self):
        assert resolve_server_exposure(_cfg(exposure="hidden"), "direct") == "hidden"
        assert resolve_server_exposure(_cfg(exposure="bogus"), "codemode") == "codemode"
        assert resolve_server_exposure(_cfg(), "deferred") == "deferred"
        assert resolve_server_exposure(_cfg(), "garbage") == "direct"


# ── end to end through the dispatcher ───────────────────────

from mocode.core.agent import AgentConfig
from mocode.core.dispatch import ToolDispatcher
from mocode.core.events import ToolCallFinished, ToolCallStarted
from mocode.core.hook import HookRunner


async def _dispatch(host, name: str, args: dict, *, origin: str = "model"):
    """One call through the real dispatcher pipeline; events come back too."""
    events: list = []

    async def publish(event, *, fold: bool) -> None:
        events.append((event, fold))

    dispatcher = ToolDispatcher(host.ctx.tools, HookRunner(), AgentConfig(), publish)
    result = await asyncio.wait_for(
        dispatcher.run(name, args, origin=origin), BOUND
    )
    return result, events


async def _wait_registered(runtime: McpRuntime, key: str, full_name: str) -> None:
    for _ in range(200):
        if full_name in runtime._registered.get(key, {}):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{full_name} never registered")


class TestEndToEnd:
    async def test_the_model_reaches_a_direct_tool_through_the_dispatcher(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "e2e_direct.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {"mcpServers": _demo_servers(tmp_path, script, demo={})},
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)

        result, events = await _dispatch(host, "mcp__demo__search", {"q": "x"})
        assert result.status == "ok"
        assert result.content == "hello"
        assert result.details["structured_content"] == {"ok": True}
        started = [e for e, _ in events if isinstance(e, ToolCallStarted)]
        finished = [e for e, _ in events if isinstance(e, ToolCallFinished)]
        assert len(started) == len(finished) == 1
        assert finished[0].status == "ok"
        host.close()

    async def test_program_origin_reaches_codemode_tools_and_the_model_cannot(
        self, plugin_host, tmp_path
    ):
        script = write_server(tmp_path, "e2e_cm.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script, demo={"exposure": "codemode"}
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        runtime = host.ctx.tools.get("mcp_status").mcp_runtime
        await _wait_registered(runtime, "demo", "mcp__demo__search")

        # a program call (what a codemode script would make) succeeds
        result, _ = await _dispatch(
            host, "mcp__demo__search", {"q": "x"}, origin="program"
        )
        assert result.status == "ok"
        # the model's origin cannot see or run it
        result, _ = await _dispatch(host, "mcp__demo__search", {"q": "x"})
        assert result.status == "denied"
        # the anchor answers program calls too
        result, _ = await _dispatch(host, "mcp_status", {}, origin="program")
        assert result.status == "ok"
        assert result.details["servers"][0]["name"] == "demo"
        result, _ = await _dispatch(host, "mcp_status", {})
        assert result.status == "denied"
        host.close()

    async def test_hidden_tools_refuse_every_origin(self, plugin_host, tmp_path):
        script = write_server(tmp_path, "e2e_hidden.py", MODERN_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script, demo={"exposure": "hidden"}
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        for origin in ("model", "program"):
            result, _ = await _dispatch(
                host, "mcp__demo__search", {"q": "x"}, origin=origin
            )
            assert result.status == "denied"
        host.close()

    async def test_close_kills_every_child(self, plugin_host, tmp_path):
        script_a = write_server(tmp_path, "e2e_a.py", MODERN_SERVER)
        script_b = write_server(tmp_path, "e2e_b.py", LEGACY_SERVER)
        write_mcp_json(
            tmp_path / ".mocode" / "mcp.json",
            {
                "mcpServers": _demo_servers(
                    tmp_path, script_a, a={}, b={"args": [str(script_b)]}
                )
            },
        )
        host = plugin_host(plugins=[PLUGIN], build=True, assemble=True)
        await asyncio.wait_for(host.materialize(), BOUND)
        runtime = host.ctx.tools.get("mcp_status").mcp_runtime
        pids = [_read_pid(_child_pidfile(tmp_path, "a")), _read_pid(_child_pidfile(tmp_path, "b"))]
        assert len(runtime.sessions) == 2
        assert all(child_alive(pid) for pid in pids)

        host.close()
        for pid in pids:
            assert await wait_gone(pid)

    def test_the_package_exports_the_plugin_surface(self):
        from mocode.host.plugin.builtin.mcp import (
            PLUGIN as exported,
            McpPlugin,
            McpRuntime,
        )

        assert isinstance(exported, McpPlugin)
        assert exported.name == "mcp"
        assert McpRuntime is not None
