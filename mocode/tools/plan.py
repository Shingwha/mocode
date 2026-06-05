"""PlanTool — LLM 注册活动 plan 的工具。"""

from __future__ import annotations

from ..core.tool import Tool, ToolError


def PlanTool(app) -> Tool:
    """Create a tool that registers an active plan after the LLM writes it to disk."""

    async def _handle(args: dict) -> str:
        action = args.get("action", "status")

        if action == "done":
            path = args.get("path", "")
            if not path:
                raise ToolError("'path' is required for 'done' action", "invalid_input")
            app.active_plan_path = path
            return (
                f"Plan registered: {path}\n"
                f"Tell the user: /plan:start (keep context) or /plan:start-clean (clear context)"
            )

        if action == "status":
            p = app.active_plan_path
            if not p:
                return "No active plan"
            return f"Active plan: {p}"

        if action == "clear":
            app.active_plan_path = None
            return "Active plan cleared"

        raise ToolError(f"Unknown action: {action}", "invalid_input")

    return Tool(
        "plan",
        "Register a plan file as the active plan. "
        "Call action='done' after writing the plan with write(). "
        "Tells the user to run /plan:start or /plan:start-clean to execute.",
        {
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
        },
        _handle,
    )
