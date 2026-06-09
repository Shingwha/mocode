"""PlanTool — LLM 注册活动 plan 的工具 + PlanRegistry 双位置扫描。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.tool import Tool, ToolError


class PlanRegistry:
    """Discover plan files from project-local + global dirs.

    Follows the same dual-location pattern as WorkflowRegistry.
    Each call to ``list()`` / ``get()`` re-scans directories so newly added
    plan files are picked up immediately.
    """

    def __init__(self, dirs: list[Path] | None = None):
        self._dirs: list[Path] = list(dirs) if dirs else []

    @classmethod
    def from_default_dirs(cls) -> PlanRegistry:
        """Create registry with default global + project-local dirs."""
        dirs: list[Path] = []
        local_dir = Path.cwd() / ".mocode" / "plans"
        global_dir = Path.home() / ".mocode" / "plans"
        # project-local scanned first; global scanned second and overrides
        dirs.append(local_dir)
        if local_dir != global_dir:
            dirs.append(global_dir)
        return cls(dirs=dirs)

    def _scan(self) -> dict[str, Path]:
        """Scan all directories and return plans keyed by stem name."""
        plans: dict[str, Path] = {}
        for d in self._dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                plans[f.stem] = f  # later dirs (global) override earlier
        return plans

    def list(self) -> dict[str, Path]:
        """Return all discovered plans (stem → path)."""
        return self._scan()

    def get(self, name: str) -> Path | None:
        """Find a plan by stem name (without .md extension)."""
        return self._scan().get(name)

    def location(self, name: str) -> str | None:
        """Return 'local' or 'global' for where a plan currently resides."""
        path = self.get(name)
        if path is None:
            return None
        local_dir = Path.cwd() / ".mocode" / "plans"
        global_dir = Path.home() / ".mocode" / "plans"
        try:
            path.relative_to(local_dir)
            return "local"
        except ValueError:
            pass
        try:
            path.relative_to(global_dir)
            return "global"
        except ValueError:
            return None


@dataclass
class PlanState:
    """Shared mutable state for active plan tracking.

    Injected into PlanTool and owned by CLIApp. Both tools and plan
    commands read/write through this single object.
    """
    active_plan_path: str | None = None

_PLAN_PARAMS = {
    "action": {"type": "string", "description": "Action to perform", "enum": ["done", "status", "clear"], "default": "status"},
    "path": {"type": "string", "description": "Path to the plan file (required for 'done' action)", "optional": True},
}
_PLAN_DESC = "Register a plan file as the active plan. Call action='done' after writing the plan with write(). Tells the user to run /plan:start or /plan:start-clean to execute."


class PlanTool(Tool):
    """Register an active plan after writing it to disk."""
    def __init__(self, plan_state: PlanState) -> None:
        self._plan_state = plan_state
        super().__init__(name="plan", description=_PLAN_DESC, params=_PLAN_PARAMS, func=self._execute)

    async def _execute(self, args: dict) -> str:
        action = args.get("action", "status")
        if action == "done":
            path = args.get("path", "")
            if not path:
                raise ToolError("'path' is required for 'done' action", "invalid_input")
            self._plan_state.active_plan_path = path
            return f"Plan registered: {path}\nTell the user the plan file is at {path}, and they can run /plan:start (keep context) or /plan:start-clean (clear context) to execute."
        if action == "status":
            p = self._plan_state.active_plan_path
            if not p:
                return "No active plan"
            return f"Active plan: {p}"
        if action == "clear":
            self._plan_state.active_plan_path = None
            return "Active plan cleared"
        raise ToolError(f"Unknown action: {action}", "invalid_input")
