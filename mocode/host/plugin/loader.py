"""Plugin discovery — directory scanning and module import.

Third-party plugins live in::

    ./.mocode/plugins/       project-local (wins on name conflicts)
    ~/.mocode/plugins/       user-global

Each entry is either a directory ``<name>/`` holding ``PLUGIN.md`` (metadata and
docs) plus an optional ``plugin.py``, or a single ``<name>.py`` file.

Discovery never imports anything — code is only executed for plugins that are
enabled and about to be built. Plugins are trusted code: importing ``plugin.py``
runs it.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

from .base import Plugin

PLUGIN_MD = "PLUGIN.md"
PLUGIN_PY = "plugin.py"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from *text*.

    Returns ``(frontmatter_dict, body_text)``; no frontmatter → ``({}, text)``.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml

        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}, text
    return (fm if isinstance(fm, dict) else {}), parts[2].strip()


@dataclass
class PluginSpec:
    """One discovered plugin, before its code is imported."""

    name: str
    description: str = ""
    enabled: bool = True
    entrypoint: str = ""
    path: Path | None = None  # module to import (plugin.py or <name>.py)
    source: str = ""  # origin, for log messages


def discover(search_dirs: list[Path], *, reserved: set[str] | None = None) -> list[PluginSpec]:
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
    """Import *spec* and return its plugin instance. ``None`` if it cannot load."""
    if spec.path is None or not spec.path.is_file():
        return None
    module = _import(spec.path, spec.name)
    if module is None:
        return None
    plugin = _resolve_plugin(module, spec.entrypoint, spec.name)
    if plugin is None:
        return None
    if not plugin.name:
        plugin.name = spec.name
    if not plugin.description:
        plugin.description = spec.description
    return plugin


# ── Discovery helpers ───────────────────────────────────────


def _spec_from_entry(entry: Path) -> PluginSpec | None:
    if entry.is_dir():
        meta_path = entry / PLUGIN_MD
        if not meta_path.is_file():
            return None
        fm, _body = parse_frontmatter(_read(meta_path))
        code_path = entry / PLUGIN_PY
        return PluginSpec(
            name=str(fm.get("name") or entry.name),
            description=str(fm.get("description") or ""),
            enabled=bool(fm.get("enabled", True)),
            entrypoint=str(fm.get("entrypoint") or ""),
            path=code_path if code_path.is_file() else None,
            source=str(entry),
        )

    if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
        return PluginSpec(name=entry.stem, path=entry, source=str(entry))

    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# ── Import helpers ──────────────────────────────────────────


def _import(path: Path, name: str) -> types.ModuleType | None:
    module_name = "mocode_plugin_" + "".join(c if c.isalnum() else "_" for c in name)
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
        print(f"[plugin] failed to import {path}: {e}", file=sys.stderr)
        return None


def _resolve_plugin(
    module: types.ModuleType, entrypoint: str, fallback_name: str
) -> Plugin | None:
    """Resolve the plugin: explicit ``entrypoint`` (class or instance), then a
    module-level ``plugin`` instance, then the first Plugin subclass the module
    defines itself."""
    if entrypoint:
        return _instantiate(getattr(module, entrypoint, None), fallback_name)

    instance = getattr(module, "plugin", None)
    if isinstance(instance, Plugin):
        return instance

    for obj in vars(module).values():
        if (
            isinstance(obj, type)
            and issubclass(obj, Plugin)
            and obj is not Plugin
            and getattr(obj, "__module__", "") == module.__name__
        ):
            return _instantiate(obj, fallback_name)
    return None


def _instantiate(obj: object, fallback_name: str) -> Plugin | None:
    if isinstance(obj, Plugin):
        return obj
    if isinstance(obj, type) and issubclass(obj, Plugin):
        return obj()
    print(
        f"[plugin] {fallback_name}: entrypoint does not resolve to a Plugin",
        file=sys.stderr,
    )
    return None
