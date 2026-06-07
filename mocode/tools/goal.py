"""GoalTool — LLM-callable tool for goal lifecycle management.
GoalHook lives in mocode/hooks/goal.py.
"""
from __future__ import annotations

from ..core.tool import Tool, ToolError

_GOAL_PARAMS = {
    "action": {"type": "string", "description": "Action to perform", "enum": ["set", "pause", "resume", "status", "clear"], "default": "status"},
    "goal": {"type": "string", "description": "The goal condition to set (required for 'set' action)", "optional": True},
}
_GOAL_DESC = "Manage a goal condition that the agent works toward. Actions: set (create goal), pause (temporarily stop), resume (continue paused goal), status (check state), clear (remove goal)."


class GoalTool(Tool):
    """Manage a goal condition."""
    def __init__(self, goal_hook) -> None:
        self._hook = goal_hook
        super().__init__(name="goal", description=_GOAL_DESC, params=_GOAL_PARAMS, func=self._execute)

    async def _execute(self, args: dict) -> str:
        action = args.get("action", "status")
        if action == "set":
            goal = args.get("goal", "").strip()
            if not goal:
                raise ToolError("'goal' is required for 'set' action", "invalid_input")
            self._hook.set_goal(goal)
            return f"Goal set: {goal}"
        if action == "pause":
            if not self._hook.condition:
                return "No active goal to pause"
            if self._hook.paused:
                return f"Goal already paused: {self._hook.condition}"
            self._hook.pause_goal()
            return f"Goal paused: {self._hook.condition}"
        if action == "resume":
            if not self._hook.condition:
                return "No goal to resume"
            if not self._hook.paused:
                return f"Goal is not paused: {self._hook.condition}"
            self._hook.resume_goal()
            return f"Goal resumed: {self._hook.condition} (turn counter reset, {self._hook.max_turns} turns remaining)"
        if action == "clear":
            condition = self._hook.condition
            self._hook.clear_goal()
            if condition:
                return f"Goal cleared: {condition}"
            return "No active goal to clear"
        # status
        condition = self._hook.condition
        if not condition:
            return "No active goal"
        state = "paused" if self._hook.paused else "active"
        return f"Goal: {condition}\nState: {state}\nTurn: {self._hook.turn_count}/{self._hook.max_turns}"
