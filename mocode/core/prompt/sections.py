"""内置 Prompt 片段定义"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .builder import DynamicSection, StaticSection

if TYPE_CHECKING:
    from ...skills.manager import SkillManager

# === 优先级常量 ===
PRIORITY_IDENTITY = 10  # 身份定义 (最前)
PRIORITY_ENVIRONMENT = 20  # 环境信息
PRIORITY_TOOLS = 30  # 工具列表
PRIORITY_SKILLS = 40  # Skills 列表
PRIORITY_BEHAVIOR = 50  # 行为准则
PRIORITY_CUSTOM = 100  # 自定义内容 (最后)

# === 内置片段 ===

IDENTITY_SECTION = StaticSection(
    name="identity",
    priority=PRIORITY_IDENTITY,
    content="You are MoCode, a concise coding assistant.",
)


def _render_environment(ctx: dict[str, Any]) -> str:
    from ...paths import CONFIG_PATH, MOCODE_HOME, SESSIONS_DIR, SKILLS_DIR

    cwd = ctx.get("cwd") or os.getcwd()
    lines = [
        "## Environment",
        f"- Working directory: {cwd}",
        f"- MoCode home: {MOCODE_HOME}",
        f"- MoCode config file: {CONFIG_PATH}",
        f"- Skills directory: {SKILLS_DIR}",
        f"- Sessions directory: {SESSIONS_DIR}",
    ]
    return "\n".join(lines)


ENVIRONMENT_SECTION = DynamicSection(
    name="environment", priority=PRIORITY_ENVIRONMENT, renderer=_render_environment
)


def _render_tools(ctx: dict[str, Any]) -> str:
    """渲染工具列表

    注意: 必须在 register_all_tools() 之后调用
    """
    from ...tools.base import ToolRegistry

    tools = ToolRegistry.all()
    if not tools:
        return ""
    lines = ["You have access to the following tools:"]
    for tool in tools:
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


TOOLS_SECTION = DynamicSection(
    name="tools", priority=PRIORITY_TOOLS, renderer=_render_tools
)


def _render_skills(ctx: dict[str, Any]) -> str:
    skill_manager = ctx.get("skill_manager")
    if not skill_manager:
        return ""
    skills = skill_manager.get_all_metadata()
    if not skills:
        return ""
    lines = [
        "## Available Skills",
        "",
        "Use the `skill` tool to load detailed instructions:",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


SKILLS_SECTION = DynamicSection(
    name="skills", priority=PRIORITY_SKILLS, renderer=_render_skills
)


BEHAVIOR_SECTION = StaticSection(
    name="behavior",
    priority=PRIORITY_BEHAVIOR,
    content="""Guidelines:
- Be concise and direct
- Prefer `edit` over `write` for existing files
- Verify changes before claiming success
- Handle errors gracefully
- Respond in the same language the user uses""",
)
