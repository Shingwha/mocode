"""Plugin discovery — the Agent Plugins layout, and the module import.

A plugin is a directory::

    acme/
    ├── plugin.json            the manifest: name, version, description (standard)
    ├── skills/<name>/SKILL.md portable skills, readable by any client (standard)
    ├── mcp.json               portable MCP servers (standard; recognised, not served yet)
    ├── mocode/plugin.py       MoCode contributions — build(ctx) (our namespace)
    └── mocode.cli/plugin.py   the terminal's own contributions (the terminal's namespace)

Or a single ``<name>.py`` file: the shortcut for a MoCode-only plugin with no
portable parts.

The split is the standard's, and it is the whole point of the layout: the root
of a plugin directory holds what any compatible client understands, and
everything client-specific lives under a directory named for the namespace that
defines it. Another client reading ``acme/`` picks up ``skills/``, ignores
``mocode/`` and ``mocode.cli/`` without validating them, and vice versa. The
host only ever hands namespace directories over; it never looks inside one.

Discovery imports nothing. Code is executed only for plugins that are enabled
and about to be built — plugins are trusted code, so importing one runs it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from .base import Plugin
from .env import PluginVenv

MANIFEST = "plugin.json"
CODE_MODULE = "plugin.py"

#: The namespace MoCode's own plugin code lives in. Contributions here use the
#: host API only, so every MoCode frontend gets them.
HOST_NAMESPACE = "mocode"

#: Top-level manifest fields the standard defines. Anything else is reported
#: and ignored — the manifest schema is closed, and MoCode's own extras belong
#: under ``extensions``.
MANIFEST_FIELDS = frozenset(
    {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
)

_NAME_ALLOWED = re.compile(r"^[a-z0-9.-]+$")


def report(message: str) -> None:
    """The one way a plugin problem is said: stderr, prefixed, never fatal."""
    print(f"[plugin] {message}", file=sys.stderr)


@dataclass
class PluginSpec:
    """One discovered plugin, before its code is imported."""

    name: str
    description: str = ""
    version: str = ""
    #: The plugin's directory — where its portable parts and its namespaces live.
    directory: Path | None = None
    #: The MoCode entry module to import, if the plugin ships code.
    module: Path | None = None
    #: Where the plugin was found, for log messages.
    source: str = ""


def valid_name(name: str) -> bool:
    """Whether *name* satisfies the standard's plugin-name rule."""
    if not 1 <= len(name) <= 64:
        return False
    if not (name[0].isalnum() and name[-1].isalnum()):
        return False
    if "--" in name or ".." in name:
        return False
    return bool(_NAME_ALLOWED.match(name)) and name == name.lower()


def discover(
    search_dirs: list[Path], *, reserved: set[str] | None = None
) -> list[PluginSpec]:
    """Scan *search_dirs* in priority order and return deduplicated specs.

    Names in *reserved* (the built-ins) and repeats of an already-seen name are
    skipped: a plugin is behaviour injection, so one name must resolve to
    exactly one implementation.
    """
    reserved = reserved or set()
    seen: set[str] = set()
    specs: list[PluginSpec] = []

    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            spec = _spec_from_entry(entry)
            if spec is None or spec.name in reserved or spec.name in seen:
                continue
            seen.add(spec.name)
            specs.append(spec)

    return specs


def load_plugin(spec: PluginSpec) -> Plugin | None:
    """Import *spec*'s MoCode code and return its plugin instance.

    ``None`` when the plugin ships no code (skills-only plugins are ordinary) or
    when it cannot be imported — a broken plugin must not take the host down.
    """
    if spec.module is None or not spec.module.is_file():
        return None
    if spec.directory is not None:
        # Before the import, so packages the module needs resolve while it
        # loads — and it stays for the process, so its lazy imports do too.
        # See env.py for what attaching does and does not promise.
        PluginVenv(spec.directory).attach()
    module = import_module_file(spec.module, f"mocode_plugin_{slugify(spec.name)}")
    if module is None:
        return None
    plugin = resolve_plugin(module, Plugin, fallback_name=spec.name)
    if plugin is None:
        return None
    if not plugin.name:
        plugin.name = spec.name
    if not plugin.description:
        plugin.description = spec.description
    return plugin


def namespace_dir(source: "Path | PluginSpec", namespace: str) -> Path | None:
    """The directory *source* ships for *namespace*, if it ships one.

    Takes a plugin directory or a whole spec — a frontend holding sources and
    the host holding specs ask the same question. The host never looks inside:
    a namespace belongs to whoever declared it.
    """
    directory = source if isinstance(source, Path) else source.directory
    if directory is None:
        return None
    candidate = directory / namespace
    return candidate if candidate.is_dir() else None


# ── Discovery helpers ───────────────────────────────────────


def read_manifest(path: Path) -> PluginSpec | None:
    """Read a ``plugin.json`` into a spec, or ``None`` if it is not usable.

    The standard's rules: the schema is closed (unknown top-level fields are
    reported and ignored), and a manifest that violates it rejects the plugin
    rather than half-loading it.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        report(f"{path}: unreadable manifest: {e}")
        return None

    if not isinstance(data, dict):
        report(f"{path}: manifest must be an object")
        return None

    name = str(data.get("name") or "")
    if not valid_name(name):
        report(
            f"{path}: invalid name {name!r} — lowercase alphanumerics, "
            "'-', '.', 1-64 characters, no '--' or '..'"
        )
        return None

    unknown = sorted(set(data) - MANIFEST_FIELDS)
    if unknown:
        report(
            f"{name}: unknown manifest field(s) {', '.join(unknown)} (ignored)"
        )
    return PluginSpec(
        name=name,
        description=str(data.get("description") or ""),
        version=str(data.get("version") or ""),
        directory=path.parent,
        source=str(path.parent),
    )


def _spec_from_entry(entry: Path) -> PluginSpec | None:
    if entry.is_dir():
        if not (entry / MANIFEST).is_file():
            return None
        spec = read_manifest(entry / MANIFEST)
        if spec is None:
            return None
        code = entry / HOST_NAMESPACE / CODE_MODULE
        spec.module = code if code.is_file() else None
        return spec

    if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
        return PluginSpec(name=entry.stem, module=entry, source=str(entry))

    return None


def slugify(name: str) -> str:
    """A module-name-safe rendering of a plugin name (``my-tool`` → ``my_tool``)."""
    return "".join(c if c.isalnum() else "_" for c in name)


# ── Import helpers ──────────────────────────────────────────


def import_module_file(path: Path, module_name: str) -> types.ModuleType | None:
    """Import *path* as *module_name*. ``None`` if it cannot be imported."""
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:  # a broken plugin must not take the host down
        sys.modules.pop(module_name, None)
        report(f"failed to import {path}: {e}")
        if isinstance(e, ModuleNotFoundError):
            report(
                f"  {path}: needs a package mocode's environment does not "
                "have — install it there (uv pip install <package>), or give "
                "the plugin one of its own (mocode plugin sync — "
                "docs/plugins.md#dependencies)"
            )
        return None


def resolve_plugin(
    module: types.ModuleType, base: type, *, fallback_name: str
) -> object | None:
    """Resolve a plugin from an imported module, for any extension surface.

    A module-level ``plugin`` instance wins, then the first subclass of *base*
    the module defines itself. One rule for host plugins and frontend plugins
    alike: an author never has to say which of their classes is the plugin.
    """
    instance = getattr(module, "plugin", None)
    if isinstance(instance, base):
        return instance

    for obj in vars(module).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, base)
            and obj is not base
            and getattr(obj, "__module__", "") == module.__name__
        ):
            return obj()
    report(f"{fallback_name}: no {base.__name__} found in the module")
    return None
