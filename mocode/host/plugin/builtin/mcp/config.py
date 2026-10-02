"""MCP server configuration — where entries come from, and what they mean.

Four sources merge into one server table, highest priority first; a
same-named entry from a higher-priority source replaces the whole entry
from a lower-priority one. Server names that differ only in ``-`` vs ``_``
count as the same server — the second one is reported and dropped:

1. ``plugins.mcp.servers`` inline in config.json
2. ``<cwd>/.mocode/mcp.json``
3. ``<home>/mcp.json`` (``~/.mocode/mcp.json``)
4. ``mcp.json`` in each plugin source directory, in array order

Two rule sets apply, kept deliberately separate (decision D14):

* **Plugin-directory files (source 4)** follow the Agent Plugins 1.0.0
  standard strictly: the top level holds only ``$schema`` + ``mcpServers``,
  the schema must match :data:`MCP_SCHEMA_1_0_0` or the whole file is
  skipped, and entries carry only the standard field set. Keys the standard
  does not know (``exposure`` and friends) are reported and ignored — their
  presence never rejects the entry, but they have no effect here.
  ``command`` is a single executable token and is never expanded;
  ``args`` / ``env`` / ``cwd`` support single-pass ``${PLUGIN_ROOT}`` /
  ``${PLUGIN_DATA}`` expansion, and the child environment must be given
  ``PLUGIN_ROOT`` (the plugin directory) and ``PLUGIN_DATA``
  (``<plugin>/.mocode-data``, created if missing, writable). ``env`` may not
  set those two keys, and an expanded ``cwd`` must stay inside the root it
  started from.

* **mocode's own files (sources 1–3)** are mocode's own syntax: besides the
  standard fields they accept ``enabled``, ``timeout``, ``exposure`` /
  ``toolExposure`` and ``description``, expand ``${VAR}`` from the
  environment (a missing variable becomes an empty string plus a report;
  ``!command`` values are reported and used literally — v1 does not run
  them), resolve a relative ``cwd`` against the file's directory, and treat
  ``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` as meaningless — an entry using
  them is skipped.

v1 speaks stdio only. ``sse`` entries are rejected with a hint to use the
streamable HTTP endpoint (commonly ``/mcp``); ``http`` / ``streamable-http``
entries are reported and skipped (wave W3 implements that transport).
A broken file, a wrong shape or an invalid single entry is reported and
skipped — never fatal to the other servers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ...loader import report

#: The only ``$schema`` a plugin-directory ``mcp.json`` may claim (Agent
#: Plugins 1.0.0). A mismatch disables the plugin's MCP configuration.
MCP_SCHEMA_1_0_0 = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

#: The directory a plugin's ``PLUGIN_DATA`` points at, inside the plugin.
PLUGIN_DATA_DIR = ".mocode-data"

#: Field sets per the standard's server schema.
_STDIO_KEYS = frozenset({"type", "command", "args", "env", "cwd"})
_HTTP_KEYS = frozenset({"type", "url", "headers"})
#: Extra fields mocode's own files accept on top of the standard set.
_MOCODE_EXTRA_KEYS = frozenset(
    {"enabled", "timeout", "exposure", "toolExposure", "description"}
)
_TOP_LEVEL_KEYS = frozenset({"$schema", "mcpServers"})
_TRANSPORT_TYPES = frozenset({"stdio", "http", "streamable-http"})

#: ${VAR} — expanded from the environment in mocode's own files.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: ${PLUGIN_ROOT} / ${PLUGIN_DATA} — expanded only in plugin files.
_PLUGIN_TOKEN_RE = re.compile(r"\$\{(PLUGIN_ROOT|PLUGIN_DATA)\}")
#: cwd shapes a plugin stdio entry may use (standard §7.2.1).
_PLUGIN_CWD_RE = re.compile(
    r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))"
)


@dataclass
class McpServerConfig:
    """One resolved MCP server entry.

    v1 keeps stdio entries only — transport validation happens at parse
    time, so ``command`` is always present. ``env`` is the overlay merged
    over the process environment at spawn time (``PLUGIN_ROOT`` /
    ``PLUGIN_DATA`` are added by the session for plugin-sourced entries).
    ``cwd`` is already resolved to a path at parse time, or ``None`` to
    inherit the process working directory. ``exposure`` stays raw here;
    :mod:`.naming` resolves it into per-tool availability.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    timeout: float = 60.0
    exposure: str | None = None
    tool_exposure: dict[str, str] = field(default_factory=dict)
    description: str = ""
    #: Human-readable origin (``config.json``, a file path) for reports.
    source: str = ""
    #: Set for plugin-directory entries — the session injects both into the
    #: child environment.
    plugin_root: Path | None = None
    plugin_data: Path | None = None


def _fold(name: str) -> str:
    """The comparison key for server names — ``-`` and ``_`` collapse.

    Deliberately the same fold :mod:`.naming` applies; unified there once it
    exists.
    """

    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _expand_vars(value: str, environ: Mapping[str, str], source: str) -> str:
    """Expand ``${VAR}`` from *environ*; a missing variable reports and
    becomes an empty string."""

    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        if var in environ:
            return str(environ[var])
        missing.append(var)
        return ""

    out = _VAR_RE.sub(repl, value)
    for var in missing:
        report(
            f"{source}: environment variable {var!r} is not set — "
            "expanded to an empty string"
        )
    return out


def _check_no_bang(value: str, source: str, name: str, field_name: str) -> str:
    """v1 does not run ``!command`` values; report and keep them literal."""
    if value.startswith("!"):
        report(
            f"{source}: server {name!r}: {field_name} uses '!command' "
            "expansion, which is not supported (v1) — the literal value is used"
        )
    return value


def _parse_entry(
    name: str,
    entry: object,
    *,
    base_dir: Path,
    source: str,
    strict: bool,
    plugin_root: Path | None,
    plugin_data: Path | None,
    environ: Mapping[str, str],
) -> McpServerConfig | None:
    """Validate one ``mcpServers`` entry and fold it into a config.

    *strict* selects the Agent Plugins rule set (a plugin-directory file);
    otherwise mocode's own extensions apply. ``None`` means the entry was
    reported and skipped.
    """

    if not isinstance(entry, dict):
        report(f"{source}: server {name!r}: entry must be an object — skipped")
        return None

    entry_type = entry.get("type")
    if entry_type is None:
        if strict:
            report(f"{source}: server {name!r}: missing 'type' — entry skipped")
            return None
        # mocode files may omit type: a command selects stdio, a url http.
        if "url" in entry:
            entry_type = "http"
        elif "command" in entry:
            entry_type = "stdio"
        else:
            report(
                f"{source}: server {name!r}: neither 'command' nor 'url' — entry skipped"
            )
            return None
    if not isinstance(entry_type, str):
        report(f"{source}: server {name!r}: 'type' must be a string — entry skipped")
        return None

    if entry_type == "sse":
        report(
            f"{source}: server {name!r}: the legacy 'sse' transport is not "
            "supported — use the streamable HTTP endpoint (commonly '/mcp') instead; entry skipped"
        )
        return None
    if entry_type in ("http", "streamable-http"):
        report(
            f"{source}: server {name!r}: the streamable HTTP transport is not "
            "implemented (v1: stdio only; see wave W3); entry skipped"
        )
        return None
    if entry_type != "stdio":
        report(
            f"{source}: server {name!r}: unknown type {entry_type!r} "
            "(expected 'stdio', 'http' or 'streamable-http') — entry skipped"
        )
        return None

    allowed = _STDIO_KEYS if strict else _STDIO_KEYS | _MOCODE_EXTRA_KEYS
    extra = sorted(set(entry) - allowed)
    if extra:
        report(
            f"{source}: server {name!r}: unknown field(s) {', '.join(extra)} "
            "(ignored)"
        )

    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        report(
            f"{source}: server {name!r}: 'command' must be a non-empty string — entry skipped"
        )
        return None

    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        report(
            f"{source}: server {name!r}: 'args' must be a list of strings — entry skipped"
        )
        return None

    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        report(
            f"{source}: server {name!r}: 'env' must be an object of strings — entry skipped"
        )
        return None

    raw_cwd = entry.get("cwd")
    if raw_cwd is not None and not isinstance(raw_cwd, str):
        report(f"{source}: server {name!r}: 'cwd' must be a string — entry skipped")
        return None

    # ── rule-set-specific expansion and resolution ──────────
    if strict:
        for key in ("PLUGIN_ROOT", "PLUGIN_DATA"):
            if key in env:
                report(
                    f"{source}: server {name!r}: env must not set {key!r} "
                    "(provided by the client) — entry skipped"
                )
                return None
        if plugin_root is None or plugin_data is None:  # pragma: no cover - defensive
            return None
        # command is one executable token — never expanded (standard §7.2.1).
        args = [
            a.replace("${PLUGIN_ROOT}", str(plugin_root)).replace(
                "${PLUGIN_DATA}", str(plugin_data)
            )
            for a in args
        ]
        env = {
            k: v.replace("${PLUGIN_ROOT}", str(plugin_root)).replace(
                "${PLUGIN_DATA}", str(plugin_data)
            )
            for k, v in env.items()
        }
        if raw_cwd is None:
            cwd: str | None = str(plugin_root)
        else:
            if not _PLUGIN_CWD_RE.match(raw_cwd):
                report(
                    f"{source}: server {name!r}: cwd must start with './', "
                    "'${PLUGIN_ROOT}' or '${PLUGIN_DATA}' — entry skipped"
                )
                return None
            if raw_cwd.startswith("./"):
                expanded = str(Path(plugin_root) / raw_cwd[2:])
                root = plugin_root
            else:
                expanded = raw_cwd.replace("${PLUGIN_ROOT}", str(plugin_root)).replace(
                    "${PLUGIN_DATA}", str(plugin_data)
                )
                root = plugin_data if raw_cwd.startswith("${PLUGIN_DATA}") else plugin_root
            resolved = Path(expanded).resolve()
            if not resolved.is_relative_to(Path(root).resolve()):
                report(
                    f"{source}: server {name!r}: cwd {expanded!r} escapes "
                    f"{root} — entry skipped"
                )
                return None
            cwd = str(resolved)
    else:
        joined = "\n".join(
            [command, *args, *env.keys(), *env.values(), raw_cwd or ""]
        )
        if _PLUGIN_TOKEN_RE.search(joined):
            report(
                f"{source}: server {name!r}: '${{PLUGIN_ROOT}}'/"
                "'${PLUGIN_DATA}' only mean something inside a plugin "
                "directory — entry skipped"
            )
            return None
        args = [
            _check_no_bang(_expand_vars(a, environ, source), source, name, "an args entry")
            for a in args
        ]
        env = {
            k: _check_no_bang(
                _expand_vars(v, environ, source), source, name, f"env {k!r}"
            )
            for k, v in env.items()
        }
        if raw_cwd is None:
            cwd = None
        else:
            expanded_cwd = _expand_vars(raw_cwd, environ, source)
            cwd = (
                expanded_cwd
                if Path(expanded_cwd).is_absolute()
                else str((base_dir / expanded_cwd).resolve())
            )

    cfg = McpServerConfig(
        name=name,
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        source=source,
        plugin_root=plugin_root if strict else None,
        plugin_data=plugin_data if strict else None,
    )

    if not strict:
        enabled = entry.get("enabled", True)
        if isinstance(enabled, bool):
            cfg.enabled = enabled
        elif "enabled" in entry:
            report(
                f"{source}: server {name!r}: 'enabled' must be true or false — ignored"
            )
        timeout = entry.get("timeout")
        if timeout is not None:
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
                cfg.timeout = float(timeout)
            else:
                report(
                    f"{source}: server {name!r}: 'timeout' must be a positive "
                    "number of seconds — the default (60) is used"
                )
        exposure = entry.get("exposure")
        if exposure is not None:
            if isinstance(exposure, str):
                cfg.exposure = exposure
            else:
                report(
                    f"{source}: server {name!r}: 'exposure' must be a string — ignored"
                )
        tool_exposure = entry.get("toolExposure")
        if tool_exposure is not None:
            if isinstance(tool_exposure, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in tool_exposure.items()
            ):
                cfg.tool_exposure = dict(tool_exposure)
            else:
                report(
                    f"{source}: server {name!r}: 'toolExposure' must be an "
                    "object of strings — ignored"
                )
        description = entry.get("description")
        if description is not None:
            if isinstance(description, str):
                cfg.description = description
            else:
                report(
                    f"{source}: server {name!r}: 'description' must be a string — ignored"
                )
    return cfg


def _read_json_file(path: Path) -> dict | None:
    """Read one ``mcp.json``; ``None`` (reported) when unusable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        report(f"{path}: unreadable mcp.json ({e}) — file skipped")
        return None
    if not isinstance(raw, dict):
        report(f"{path}: mcp.json must contain a JSON object — file skipped")
        return None
    return raw


def _parse_mocode_file(path: Path, *, environ: Mapping[str, str]) -> dict[str, McpServerConfig]:
    """Rule set B — one of mocode's own files."""
    raw = _read_json_file(path)
    if raw is None:
        return {}
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        report(f"{path}: 'mcpServers' must be an object — file skipped")
        return {}
    out: dict[str, McpServerConfig] = {}
    for name, entry in servers.items():
        cfg = _parse_entry(
            str(name),
            entry,
            base_dir=path.parent,
            source=str(path),
            strict=False,
            plugin_root=None,
            plugin_data=None,
            environ=environ,
        )
        if cfg is not None:
            out[_fold(str(name))] = cfg
    return out


def _parse_inline(
    servers: object, *, base_dir: Path, environ: Mapping[str, str]
) -> dict[str, McpServerConfig]:
    """Rule set B for ``plugins.mcp.servers`` in config.json."""
    if not isinstance(servers, dict):
        if servers:
            report("config.json: plugins.mcp.servers must be an object — inline servers ignored")
        return {}
    out: dict[str, McpServerConfig] = {}
    for name, entry in servers.items():
        cfg = _parse_entry(
            str(name),
            entry,
            base_dir=base_dir,
            source="config.json",
            strict=False,
            plugin_root=None,
            plugin_data=None,
            environ=environ,
        )
        if cfg is not None:
            out[_fold(str(name))] = cfg
    return out


def _parse_plugin_file(path: Path, plugin_root: Path) -> dict[str, McpServerConfig]:
    """Rule set A — a plugin directory's ``mcp.json``, strict per the standard."""
    raw = _read_json_file(path)
    if raw is None:
        return {}
    extra_top = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if extra_top:
        report(
            f"{path}: unknown top-level field(s) {', '.join(extra_top)} — the "
            "plugin's MCP configuration is skipped"
        )
        return {}
    if raw.get("$schema") != MCP_SCHEMA_1_0_0:
        report(
            f"{path}: '$schema' must be {MCP_SCHEMA_1_0_0!r} — the plugin's "
            "MCP configuration is skipped"
        )
        return {}
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        report(f"{path}: 'mcpServers' must be an object — file skipped")
        return {}

    plugin_data = plugin_root / PLUGIN_DATA_DIR
    try:
        plugin_data.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        report(
            f"{path}: cannot create {plugin_data} ({e}) — the plugin's MCP "
            "configuration is skipped"
        )
        return {}

    out: dict[str, McpServerConfig] = {}
    for name, entry in servers.items():
        cfg = _parse_entry(
            str(name),
            entry,
            base_dir=plugin_root,
            source=str(path),
            strict=True,
            plugin_root=plugin_root,
            plugin_data=plugin_data,
            environ={},
        )
        if cfg is not None:
            out[_fold(str(name))] = cfg
    return out


def load_servers(
    *,
    mcp_config: object,
    cwd: Path,
    home: Path,
    plugin_sources: list[Path] | tuple[Path, ...] = (),
    environ: Mapping[str, str] | None = None,
) -> dict[str, McpServerConfig]:
    """Merge every MCP server source into one table, keyed by folded name.

    Priority (highest first): inline ``plugins.mcp.servers``, then
    ``<cwd>/.mocode/mcp.json``, ``<home>/mcp.json`` and each plugin source's
    ``mcp.json`` in array order. A same-named entry from a stronger source
    replaces the weaker one wholesale; names that differ only in ``-`` vs
    ``_`` count as the same name — the weaker entry is reported and dropped.
    """

    environ = os.environ if environ is None else environ
    if not isinstance(mcp_config, dict):
        mcp_config = {}

    merged: dict[str, McpServerConfig] = {}

    def absorb(entries: dict[str, McpServerConfig], origin: str) -> None:
        for folded, cfg in entries.items():
            if folded in merged:
                report(
                    f"{origin}: server {cfg.name!r} matches {merged[folded].name!r} "
                    f"from {merged[folded].source} — the lower-priority entry is dropped"
                )
                continue
            merged[folded] = cfg

    absorb(
        _parse_inline(mcp_config.get("servers", {}), base_dir=Path(cwd), environ=environ),
        "config.json",
    )
    project_file = Path(cwd) / ".mocode" / "mcp.json"
    if project_file.is_file():
        absorb(_parse_mocode_file(project_file, environ=environ), str(project_file))
    home_file = Path(home) / "mcp.json"
    if home_file.is_file():
        absorb(_parse_mocode_file(home_file, environ=environ), str(home_file))
    for source_dir in plugin_sources:
        plugin_file = Path(source_dir) / "mcp.json"
        if plugin_file.is_file():
            absorb(
                _parse_plugin_file(plugin_file, Path(source_dir)),
                str(plugin_file),
            )
    return merged
