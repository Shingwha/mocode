"""PlanTool — LLM 注册活动 plan 的工具。"""

from __future__ import annotations

from ..core.tool import Tool, ToolError

_PLAN_PARAMS = {
    "action": {
        "type": "string",
        "description": "Action to perform",
        "enum": ["done", "status", "clear"],
        "default": "status",
    },
    "path": {
        "type": "string",
        "description": "Path to the plan file (required for 'done' action)",
        "optional": True,
    },
}

_PLAN_DESC = (
    "Register a plan file as the active plan. "
    "Call action='done' after writing the plan with write(). "
    "Tells the user to run /plan:start or /plan:start-clean to execute."
)


class PlanTool(Tool):
    """Register an active plan after writing it to disk."""

    def __init__(self, app) -> None:
        self._app = app
        super().__init__(name="plan", description=_PLAN_DESC, params=_PLAN_PARAMS, func=self._execute)

    async def _execute(self, args: dict) -> str:
        action = args.get("action", "status")

        if action == "done":
            path = args.get("path", "")
            if not path:
                raise ToolError("'path' is required for 'done' action", "invalid_input")
            self._app.active_plan_path = path
            return (
                f"Plan registered: {path}\n"
                f"Tell the user the plan file is at {path}, "
                f"and they can run /plan:start (keep context) or /plan:start-clean (clear context) to execute."
            )

        if action == "status":
            p = self._app.active_plan_path
            if not p:
                return "No active plan"
            return f"Active plan: {p}"

        if action == "clear":
            self._app.active_plan_path = None
            return "Active plan cleared"

        raise ToolError(f"Unknown action: {action}", "invalid_input")
