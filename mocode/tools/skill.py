"""Skill tool — loads skill content on demand."""
from __future__ import annotations

from ..core.tool import Tool, ToolError
from ..core.skill import SkillManager

_SKILL_PARAMS = {"name": {"type": "string", "description": "The skill name to load"}}
_SKILL_DESC = "Load a skill by name. Use when the user's request matches a skill's description. Returns the skill's instructions for you to follow."


class SkillTool(Tool):
    """Load a skill by name."""
    def __init__(self, manager: SkillManager, *, name: str = "skill") -> None:
        self._manager = manager
        super().__init__(name=name, description=_SKILL_DESC, params=_SKILL_PARAMS, func=self._execute)

    def _execute(self, args: dict) -> str:
        skill_name = args["name"]
        skill = self._manager.get(skill_name)
        if not skill:
            available = self._manager.names()
            hint = f" Available: {available}" if available else " No skills available."
            raise ToolError(f"Skill '{skill_name}' not found.{hint}", "not_found")
        return f"Base directory: {skill.base_dir}\n\n{skill.load_content()}"
