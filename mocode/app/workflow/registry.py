"""Workflow file discovery and registry."""

from __future__ import annotations

from pathlib import Path

from .models import Workflow


class WorkflowRegistry:
    """Discovers YAML workflow files from configured directories."""

    def __init__(self, dirs: list[Path] | None = None):
        self._dirs: list[Path] = list(dirs) if dirs else []
        self._workflows: dict[str, Workflow] = {}
        self.discover()

    def discover(self) -> None:
        """Scan directories for *.yaml / *.yml files."""
        self._workflows.clear()
        for d in self._dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix in (".yaml", ".yml"):
                    wf = self._load(f)
                    if wf:
                        self._workflows[wf.name] = wf

    def _load(self, path: Path) -> Workflow | None:
        try:
            return Workflow.from_yaml(path)
        except Exception:
            return None

    def list(self) -> list[Workflow]:
        return list(self._workflows.values())

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def names(self) -> list[str]:
        return list(self._workflows.keys())
