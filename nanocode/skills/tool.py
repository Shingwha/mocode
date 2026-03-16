"""Skill 工具实现"""

from ..tools.base import Tool, ToolRegistry
from .manager import SkillManager


def _use_skill(args: dict) -> str:
    """加载并使用指定 skill

    参数:
        name: skill 名称
    """
    name = args.get("name")
    if not name:
        return "error: missing required parameter 'name'"

    manager = SkillManager.get_instance()
    skill = manager.get_skill(name)

    if not skill:
        available = manager.list_skills()
        if available:
            return f"error: skill '{name}' not found. Available skills: {available}"
        else:
            return f"error: skill '{name}' not found. No skills are currently available."

    # 加载 skill 内容
    content = skill.load_content()
    return f"Skill '{name}' loaded:\n\n{content}"


def register_skill_tools():
    """注册 skill 工具"""
    ToolRegistry.register(
        Tool(
            "skill",
            "Load a skill by name. Use when the user's request matches a skill's description. "
            "Returns the skill's instructions for you to follow.",
            {"name": "string"},
            _use_skill,
        )
    )
