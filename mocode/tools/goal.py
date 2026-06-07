"""GoalTool — LLM-callable tool for goal lifecycle management.

GoalHook lives in mocode/hooks/goal.py.
"""

from __future__ import annotations

from ..core.tool import Tool, ToolError


def GoalTool(goal_hook) -> Tool:
    """Create a tool that lets the LLM set, pause, resume, check, or clear a goal."""

    async def _handle(args: dict) -> str:
        action = args.get("action", "status")

        if action == "set":
            goal = args.get("goal", "").strip()
            if not goal:
                raise ToolError("'goal' is required for 'set' action", "invalid_input")
            goal_hook.set_goal(goal)
            return f"Goal set: {goal}"

        if action == "pause":
            if not goal_hook.condition:
                return "No active goal to pause"
            if goal_hook.paused:
                return f"Goal already paused: {goal_hook.condition}"
            goal_hook.pause_goal()
            return f"Goal paused: {goal_hook.condition}"

        if action == "resume":
            if not goal_hook.condition:
                return "No goal to resume"
            if not goal_hook.paused:
                return f"Goal is not paused: {goal_hook.condition}"
            goal_hook.resume_goal()
            return f"Goal resumed: {goal_hook.condition} (turn counter reset, {goal_hook.max_turns} turns remaining)"

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
        state = "paused" if goal_hook.paused else "active"
        return f"Goal: {condition}\nState: {state}\nTurn: {goal_hook.turn_count}/{goal_hook.max_turns}"

    return Tool(
        "goal",
        "Manage a goal condition that the agent works toward. Actions: set (create goal), pause (temporarily stop), resume (continue paused goal), status (check state), clear (remove goal).",
        {
            "action": {
                "type": "string",
                "description": "Action to perform",
                "enum": ["set", "pause", "resume", "status", "clear"],
                "default": "status",
            },
            "goal": {
                "type": "string",
                "description": "The goal condition to set (required for 'set' action)",
                "optional": True,
            },
        },
        _handle,
    )
