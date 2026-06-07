"""Workflow file discovery and registry."""

from __future__ import annotations

import logging
from pathlib import Path

from .models import Workflow

logger = logging.getLogger(__name__)


class WorkflowRegistry:
    """Discovers YAML workflow files from configured directories.

    Each call to ``list()``, ``get()`` or ``names()`` re-scans the directories
    so newly added YAML files are picked up immediately without restarting.
    """

    def __init__(self, dirs: list[Path] | None = None):
        self._dirs: list[Path] = list(dirs) if dirs else []

    @classmethod
    def from_default_dirs(cls) -> WorkflowRegistry:
        """Create registry with default global + project-local dirs."""
        dirs: list[Path] = []
        global_dir = Path.home() / ".mocode" / "workflows"
        dirs.append(global_dir)
        local_dir = Path.cwd() / ".mocode" / "workflows"
        if local_dir != global_dir:
            dirs.append(local_dir)
        return cls(dirs=dirs)

    def _scan(self) -> dict[str, Workflow]:
        """Scan all directories and return workflows."""
        workflows: dict[str, Workflow] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix in (".yaml", ".yml"):
                    wf = self._load(f)
                    if wf:
                        workflows[wf.name] = wf
        return workflows

    def _load(self, path: Path) -> Workflow | None:
        try:
            return Workflow.from_yaml(path)
        except Exception as e:
            logger.debug("Failed to load workflow %s: %s", path, e)
            return None

    def list(self) -> list[Workflow]:
        return list(self._scan().values())

    def get(self, name: str) -> Workflow | None:
        return self._scan().get(name)

    def names(self) -> list[str]:
        return list(self._scan().keys())
