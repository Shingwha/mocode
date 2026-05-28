"""GoalTool — LLM-callable tool for set/status/clear goal actions.

GoalHook lives in mocode/hooks/goal.py.
"""

from __future__ import annotations

from ..core.tool import Tool


def GoalTool(goal_hook) -> Tool:
    """Create a tool that lets the LLM set, check, or clear a goal."""

    async def _handle(args: dict) -> str:
        action = args.get("action", "status")

        if action == "set":
            goal = args.get("goal", "").strip()
            if not goal:
                return "Error: 'goal' is required for 'set' action"
            goal_hook.set_goal(goal)
            return f"Goal set: {goal}"

        if action == "clear":
            condition = goal_hook.condition
            goal_hook.clear_goal()
            if condition:
                return f"Goal cleared: {condition}"
            return "No active goal to clear"

        # status
        condition = goal_hook.condition
        if not condition:
            return "No active goal"
        return f"Goal: {condition}\nTurn: {goal_hook.turn_count}/{goal_hook._max_turns}"

    return Tool(
        "goal",
        "Set, check, or clear a goal condition that the agent will work toward.",
        {
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": ["set", "status", "clear"],
                "default": "status",
            },
            "goal": {
                "type": "string",
                "description": "The goal condition to set (required for 'set' action)",
            },
        },
        _handle,
    )
