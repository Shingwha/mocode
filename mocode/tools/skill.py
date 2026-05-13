"""Skill tool — loads skill content on demand.

Usage:
    from mocode.core.skill import SkillManager
    from mocode.tools import SkillTool

    mgr = SkillManager([Path.home() / ".mocode" / "skills"])
    agent = Agent().tools([SkillTool(mgr)]).build()
"""

from __future__ import annotations

from mocode.core import Tool, ToolError, SkillManager


def SkillTool(manager: SkillManager, *, name: str = "skill") -> Tool:
    def _use_skill(args: dict) -> str:
        skill_name = args.get("name")
        if not skill_name:
            raise ToolError("Missing required parameter 'name'", "missing_param")

        skill = manager.get(skill_name)
        if not skill:
            available = manager.names()
            hint = f" Available: {available}" if available else " No skills available."
            raise ToolError(f"Skill '{skill_name}' not found.{hint}", "not_found")

        return f"Base directory: {skill.path}\n\n{skill.load_content()}"

    return Tool(
        name,
        "Load a skill by name. Use when the user's request matches a skill's description. "
        "Returns the skill's instructions for you to follow.",
        {"name": "string"},
        _use_skill,
    )
