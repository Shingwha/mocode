"""内置 Prompt 片段定义"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .builder import DynamicSection, StaticSection

if TYPE_CHECKING:
    from ...skills.manager import SkillManager

# === 优先级常量 ===
PRIORITY_SOUL = 10  # SOUL.md 人格文件 (最前)
PRIORITY_USER = 11  # USER.md 用户画像
PRIORITY_MEMORY = 12  # MEMORY.md 长期记忆
PRIORITY_ENVIRONMENT = 20  # 环境信息
PRIORITY_TOOLS = 30  # 工具列表
PRIORITY_SKILLS = 40  # Skills 列表
PRIORITY_CUSTOM = 100  # 自定义内容 (最后)


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

# === Memory 文件 (SOUL / USER / MEMORY) ===

_DEFAULT_FILES: dict[str, str] = {
    "SOUL.md": (
        "# Identity\n"
        "You are MoCode, a concise coding assistant.\n"
        "\n"
        "# Guidelines\n"
        "- Be concise and direct\n"
        "- Prefer `edit` over `write` for existing files\n"
        "- Verify changes before claiming success\n"
        "- Handle errors gracefully\n"
        "- Respond in the same language the user uses\n"
    ),
    "USER.md": "# User Profile\n(Information about the user will be stored here. Edit this file to customize.)\n",
    "MEMORY.md": "# Long-term Memory\n(Important facts, decisions, and context will be stored here. Edit this file to add persistent knowledge.)\n",
}

_ensured = False


def _ensure_memory_dir() -> None:
    """确保 memory 目录和默认文件存在"""
    global _ensured
    if _ensured:
        return
    from ...paths import MEMORY_DIR

    _ensured = True
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in _DEFAULT_FILES.items():
        path = MEMORY_DIR / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _read_memory_file(filename: str) -> str | None:
    """读取 memory 目录下的单个文件，不存在或为空返回 None"""
    from ...paths import MEMORY_DIR

    _ensure_memory_dir()
    path = MEMORY_DIR / filename
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content if content else None


def _render_soul(ctx: dict[str, Any]) -> str:
    content = _read_memory_file("SOUL.md")
    if not content:
        return ""
    from ...paths import MEMORY_DIR

    return f"## SOUL\n(Edit: {MEMORY_DIR / 'SOUL.md'})\n\n{content}"


def _render_user(ctx: dict[str, Any]) -> str:
    content = _read_memory_file("USER.md")
    if not content:
        return ""
    from ...paths import MEMORY_DIR

    return f"## USER\n(Edit: {MEMORY_DIR / 'USER.md'})\n\n{content}"


def _render_memory(ctx: dict[str, Any]) -> str:
    content = _read_memory_file("MEMORY.md")
    if not content:
        return ""
    from ...paths import MEMORY_DIR

    return f"## MEMORY\n(Edit: {MEMORY_DIR / 'MEMORY.md'})\n\n{content}"


SOUL_SECTION = DynamicSection(
    name="soul", priority=PRIORITY_SOUL, renderer=_render_soul
)

USER_SECTION = DynamicSection(
    name="user", priority=PRIORITY_USER, renderer=_render_user
)

MEMORY_SECTION = DynamicSection(
    name="memory", priority=PRIORITY_MEMORY, renderer=_render_memory
)
