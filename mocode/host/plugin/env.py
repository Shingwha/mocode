"""PluginVenv — the optional uv environment a plugin directory carries.

A plugin that needs packages mocode does not ship declares them the standard
way, a ``pyproject.toml`` at its root, and the user materialises them with
``mocode plugin sync <name>`` — ``uv sync`` inside the plugin directory, a
``.venv`` that belongs to the plugin alone. This is the Python reading of
pi's per-package directory: one environment per plugin, created by an
explicit command, attached when the plugin loads.

What that environment is, and what it is not:

* **Private on disk.** Nothing installs into it or removes from it except
  that plugin's own sync.
* **Additive in the process, not isolated from it.** The loader *appends*
  its ``site-packages`` to ``sys.path`` before importing the plugin, so a
  package resolves there only when mocode's own environment does not have
  it. Two plugins pinning different versions of one package do not both get
  their way — the host's version wins, then whichever plugin imported first,
  because ``sys.modules`` is process-wide and cannot be partitioned. True
  isolation is a subprocess (``mcp.json``), not a second entry on
  ``sys.path``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


class PluginVenvError(Exception):
    """Why a plugin environment could not be synced."""


class PluginVenv:
    """The uv environment of one plugin directory — ``<dir>/.venv``."""

    VENV = ".venv"
    DECLARATION = "pyproject.toml"

    def __init__(self, plugin_dir: Path):
        self.plugin_dir = Path(plugin_dir)

    @property
    def root(self) -> Path:
        """Where the environment lives (or would)."""
        return self.plugin_dir / self.VENV

    @property
    def declared(self) -> bool:
        """Whether the plugin declares dependencies: a pyproject.toml at its root."""
        return (self.plugin_dir / self.DECLARATION).is_file()

    @property
    def exists(self) -> bool:
        """Whether the environment has been materialised."""
        return (self.root / "pyvenv.cfg").is_file()

    def site_packages(self) -> Path | None:
        """Where the environment keeps packages — probed, not asked of a subprocess.

        Both layouts are tried on any platform: ``Lib/site-packages`` (Windows
        venvs) and ``lib/python*/site-packages`` (POSIX venvs). ``None`` when
        there is no environment, or one that holds no packages yet.
        """
        if not self.exists:
            return None
        windows = self.root / "Lib" / "site-packages"
        if windows.is_dir():
            return windows
        matches = sorted(self.root.glob("lib/python*/site-packages"))
        return matches[0] if matches else None

    def attach(self) -> Path | None:
        """Make the environment importable: append site-packages to ``sys.path``.

        Appended, never inserted, and never twice. Returns the path that was
        attached — for a caller that wants to undo it; nothing in mocode
        does, because a plugin loads once per process and its lazily imported
        dependencies must keep resolving after the import that used them.
        """
        site = self.site_packages()
        if site is None or str(site) in sys.path:
            return None
        sys.path.append(str(site))
        return site

    def sync(self) -> str:
        """Materialise the environment: ``uv sync`` in the plugin directory.

        Returns a one-line report for whoever ran it. Raises
        :class:`PluginVenvError` — no declaration, uv missing, or uv itself
        failed — with the reason.
        """
        if not self.declared:
            raise PluginVenvError(
                f"{self.plugin_dir.name}: no {self.DECLARATION} — a plugin "
                "declares the dependencies of its own environment there "
                "(docs/plugins.md)"
            )
        uv = shutil.which("uv")
        if uv is None:
            raise PluginVenvError(
                "uv not found — install it (https://docs.astral.sh/uv/) and retry"
            )
        result = subprocess.run(
            [uv, "sync"], cwd=self.plugin_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            lines = (result.stderr or result.stdout).strip().splitlines()
            detail = lines[-1] if lines else f"exit {result.returncode}"
            raise PluginVenvError(
                f"uv sync failed for {self.plugin_dir.name}: {detail}"
            )
        return f"{self.plugin_dir.name}: environment ready ({self.root})"

    def describe(self) -> str:
        """The listing word for this environment: ``own env`` / ``declared`` / ``shared``."""
        if self.declared:
            return "own env" if self.exists else "declared"
        return "shared"
