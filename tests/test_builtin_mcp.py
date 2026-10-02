"""The mcp builtin plugin — configuration, naming, sessions, runtime and the
plugin lifecycle, tested against fake stdio MCP servers.

The fake servers are Python scripts written into ``tmp_path`` and launched
with ``sys.executable`` — no shell. POSIX-only process semantics branch on
``sys.platform``; every async path is bounded by a timeout so a broken fake
server can never hang the suite on Windows.
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
        assert cfg.timeout == 60.0

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

    def test_sse_is_rejected_with_a_hint(self, tmp_path, capsys):
        merged = load_servers(
            mcp_config={"servers": {"old": {"type": "sse", "url": "http://x/sse"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert merged == {}
        assert "'/mcp'" in capsys.readouterr().err

    def test_streamable_http_is_skipped_until_wave_w3(self, tmp_path, capsys):
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
        assert merged == {}
        assert "stdio only" in capsys.readouterr().err

    def test_type_is_optional_in_mocode_files_and_inferred(self, tmp_path):
        merged = load_servers(
            mcp_config={"servers": {"a": {"command": "x"}, "b": {"url": "http://u"}}},
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert list(merged) == ["a"]


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
        assert cfg.timeout == 60.0
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
        assert cfg.timeout == 60.0
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


# ── fake stdio MCP servers ──────────────────────────────────

import asyncio
import textwrap

import pytest_asyncio

from mocode.host.plugin.builtin.mcp.session import (
    ERA_LEGACY,
    ERA_MODERN,
    STATE_CLOSED,
    STATE_CONNECTED,
    STATE_DISCONNECTED,
    STATE_ERROR,
    McpError,
    StdioSession,
)

MODERN_SERVER = r'''
import json, sys, time

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

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
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": PAGE_2}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": PAGE_1, "nextCursor": "page-2"}})
    elif method == "tools/call":
        name = params.get("name")
        if "_meta" not in params:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "missing _meta"}})
        elif name == "fail":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "boom"}], "isError": True}})
        elif name == "ask":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "input_required", "inputRequests": {"login": {"method": "elicitation/create", "params": {}}}, "requestState": "opaque"}})
        elif name == "sleep":
            time.sleep(3)
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "woke up"}]}})
        elif name == "pic":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [
                {"type": "text", "text": "here:"},
                {"type": "image", "data": "QUJD", "mimeType": "image/png"}],
                "structuredContent": {"n": 1}}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "content": [{"type": "text", "text": "hello"}], "structuredContent": {"ok": True}}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''

LEGACY_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

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
        if "_meta" in params:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "legacy servers reject _meta"}})
        else:
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

NEGOTIATING_SERVER = r'''
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
    meta = (req.get("params") or {}).get("_meta") or {}
    if method == "server/discover":
        version = meta.get("io.modelcontextprotocol/protocolVersion")
        if version == "2026-07-28":
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "unsupported version", "data": {"supported": ["2026-08-30", "2025-11-25"]}}})
        elif version == "2026-08-30":
            send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "supportedVersions": ["2026-08-30"], "capabilities": {"tools": {}}, "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "negotiated", "version": "3"}}}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "unsupported version", "data": {"supported": ["2026-08-30"]}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete", "tools": [{"name": "probe", "description": "d", "inputSchema": {"type": "object", "properties": {}}}]}})
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unknown"}})
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
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32022, "message": "unsupported version", "data": {"supported": ["2025-11-25"]}}})
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

SILENT_SERVER = r'''
import sys
for line in sys.stdin:
    pass
'''

BOUND = 15  # seconds — every session operation in this file stays bounded


def write_server(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


def server_config(
    script: Path, *, name: str = "demo", timeout: float = 30.0, **kwargs
) -> McpServerConfig:
    kwargs.setdefault("source", "test")
    return McpServerConfig(
        name=name, command=sys.executable, args=[str(script)], timeout=timeout, **kwargs
    )


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """Build StdioSessions over fake scripts; close them all on teardown."""
    created: list[StdioSession] = []

    def factory(code: str, *, name: str = "demo", timeout: float = 30.0,
                discover_timeout: float = 5.0, **kwargs) -> StdioSession:
        script = write_server(tmp_path, f"server_{len(created)}.py", code)
        session = StdioSession(
            server_config(script, name=name, timeout=timeout),
            discover_timeout=discover_timeout,
            **kwargs,
        )
        created.append(session)
        return session

    yield factory
    for session in created:
        try:
            await asyncio.wait_for(session.close(), BOUND)
        except Exception:
            session.shutdown()


class TestModernSession:
    async def test_era_is_modern_with_server_info_and_instructions(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-07-28"
        assert session.server_info == {"name": "modern-srv", "version": "2.0"}
        assert session.instructions == "Modern server instructions."
        assert session.state == STATE_CONNECTED

    async def test_every_modern_request_carries_meta(self, session_factory):
        """The fake server errors any tools/* call missing _meta."""
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        tools = await asyncio.wait_for(session.list_tools(), BOUND)
        assert [t["name"] for t in tools] == ["search", "fail", "ask", "pic"]
        result = await asyncio.wait_for(session.call_tool("search", {"q": "x"}), BOUND)
        assert result["structuredContent"] == {"ok": True}

    async def test_is_error_results_come_back_untouched(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        result = await asyncio.wait_for(session.call_tool("fail"), BOUND)
        assert result["isError"] is True
        assert result["content"][0]["text"] == "boom"

    async def test_input_required_raises(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("ask"), BOUND)
        assert err.value.code == "mcp_input_required"
        assert "elicitation is not supported" in str(err.value)

    async def test_non_text_content_blocks_pass_through(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        result = await asyncio.wait_for(session.call_tool("pic"), BOUND)
        assert result["content"][1]["type"] == "image"
        assert result["structuredContent"] == {"n": 1}

    async def test_a_slow_call_times_out(self, session_factory):
        session = session_factory(MODERN_SERVER, timeout=0.4)
        await asyncio.wait_for(session.connect(), BOUND)
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(session.call_tool("sleep"), BOUND)
        assert err.value.code == "mcp_timeout"
        assert session.last_error and "timed out" in session.last_error

    async def test_stderr_is_collected_into_a_tail(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        await asyncio.wait_for(session.list_tools(), BOUND)
        assert "modern server starting" in session.stderr_tail

    async def test_a_dropped_server_disconnects_pending_calls(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        proc = session._proc
        # a request in flight when the child dies must fail, not hang
        task = asyncio.create_task(session.call_tool("sleep"))
        await asyncio.sleep(0.3)
        if sys.platform == "win32":
            proc.kill()
        else:
            proc.terminate()
        with pytest.raises(McpError) as err:
            await asyncio.wait_for(task, BOUND)
        assert "disconnected" in str(err.value)
        assert session.state == STATE_DISCONNECTED

    async def test_reconnect_after_a_drop_keeps_the_cached_era(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        proc = session._proc
        if sys.platform == "win32":
            proc.kill()
        else:
            proc.terminate()
        await asyncio.wait_for(proc.wait(), BOUND)
        tools = await asyncio.wait_for(session.list_tools(), BOUND)
        assert session.era == ERA_MODERN
        assert len(tools) == 4
        assert session._proc is not proc

    async def test_close_terminates_the_child(self, session_factory):
        session = session_factory(MODERN_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        proc = session._proc
        await asyncio.wait_for(session.close(), BOUND)
        await asyncio.wait_for(proc.wait(), BOUND)
        assert proc.returncode is not None  # the direct child is gone
        assert session.state == STATE_CLOSED

    async def test_an_unstartable_command_is_an_error(self, tmp_path):
        cfg = server_config(tmp_path / "missing.py")
        cfg.command = "definitely-not-a-real-binary-mocode"
        session = StdioSession(cfg)
        try:
            with pytest.raises(McpError) as err:
                await asyncio.wait_for(session.connect(), BOUND)
            assert "failed to start" in str(err.value)
            assert session.state == STATE_ERROR
            assert session.last_error
        finally:
            session.shutdown()


class TestLegacySession:
    async def test_era_is_legacy_after_the_handshake(self, session_factory):
        session = session_factory(LEGACY_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "legacy-srv", "version": "1.0"}
        assert session.instructions == "Legacy server instructions."
        assert session.state == STATE_CONNECTED

    async def test_legacy_requests_carry_no_meta(self, session_factory):
        """The fake server errors any call that smuggles _meta in."""
        session = session_factory(LEGACY_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        tools = await asyncio.wait_for(session.list_tools(), BOUND)
        assert [t["name"] for t in tools] == ["echo"]
        result = await asyncio.wait_for(session.call_tool("echo", {"x": "1"}), BOUND)
        assert result["structuredContent"] == {"legacy": True}

    async def test_the_list_changed_notification_fires_the_callback(self, session_factory):
        seen: list[str] = []

        async def on_tools_changed(session: StdioSession) -> None:
            tools = await session.list_tools()
            seen.extend(t["name"] for t in tools)

        session = session_factory(LEGACY_SERVER, on_tools_changed=on_tools_changed)
        await asyncio.wait_for(session.connect(), BOUND)
        await asyncio.wait_for(session.call_tool("echo", {"x": "1"}), BOUND)
        await asyncio.wait_for(session.call_tool("add_tool"), BOUND)
        await asyncio.sleep(0.2)
        assert "late" in seen


class TestEraNegotiation:
    async def test_a_negotiating_modern_server_retries_at_a_supported_version(self, session_factory):
        session = session_factory(NEGOTIATING_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        assert session.era == ERA_MODERN
        assert session.protocol_version == "2026-08-30"
        assert session.server_info == {"name": "negotiated", "version": "3"}

    async def test_no_newer_supported_version_falls_back_to_legacy(self, session_factory):
        session = session_factory(HYBRID_SERVER)
        await asyncio.wait_for(session.connect(), BOUND)
        assert session.era == ERA_LEGACY
        assert session.protocol_version == "2025-11-25"
        assert session.server_info == {"name": "hybrid-srv", "version": "1.0"}

    async def test_a_silent_server_times_out_the_probe_and_errors(self, session_factory):
        session = session_factory(SILENT_SERVER, timeout=0.4, discover_timeout=0.3)
        with pytest.raises(McpError):
            await asyncio.wait_for(session.connect(), BOUND)
        # discover timed out → treated as legacy → initialize timed out → error
        assert session.era == ERA_LEGACY
        assert session.state == STATE_ERROR
        assert session.last_error
