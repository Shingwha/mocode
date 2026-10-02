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
