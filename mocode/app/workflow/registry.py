"""Workflow file discovery and registry."""

from __future__ import annotations

from pathlib import Path

from .models import Workflow


class WorkflowRegistry:
    """Discovers YAML workflow files from configured directories.

    YAML parsing is deferred until the first ``list()`` / ``get()`` / ``names()``
    call so that construction is cheap.
    """

    def __init__(self, dirs: list[Path] | None = None):
        self._dirs: list[Path] = list(dirs) if dirs else []
        self._workflows: dict[str, Workflow] = {}
        self._loaded: bool = False

    def _ensure_loaded(self) -> None:
        """Parse all YAML files on first access."""
        if not self._loaded:
            self._loaded = True
            self._workflows.clear()
            for d in self._dirs:
                if not d.is_dir():
                    continue
                for f in sorted(d.iterdir()):
                    if f.is_file() and f.suffix in (".yaml", ".yml"):
                        wf = self._load(f)
                        if wf:
                            self._workflows[wf.name] = wf

    def discover(self) -> None:
        """Invalidate the cache so the next access re-scans."""
        self._loaded = False
        self._workflows.clear()

    def _load(self, path: Path) -> Workflow | None:
        try:
            return Workflow.from_yaml(path)
        except Exception:
            return None

    def list(self) -> list[Workflow]:
        self._ensure_loaded()
        return list(self._workflows.values())

    def get(self, name: str) -> Workflow | None:
        self._ensure_loaded()
        return self._workflows.get(name)

    def names(self) -> list[str]:
        self._ensure_loaded()
        return list(self._workflows.keys())
