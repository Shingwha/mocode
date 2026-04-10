"""Prompt building system"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ...skills.manager import SkillManager


@dataclass(slots=True)
class Section:
    """A prompt section with lazy rendering"""

    name: str
    priority: int
    render: Callable[[dict[str, Any]], str]
    enabled: bool = True


@dataclass
class PromptContributions:
    """Plugin contributions to the prompt system

    Plugins return this from get_prompt_sections() to declare how they
    want to modify the prompt: add new sections, disable built-in ones,
    or replace existing sections entirely.
    """

    add: list[Section] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    replace: dict[str, Section] = field(default_factory=dict)


class PromptBuilder:
    """Prompt builder with section management"""

    def __init__(self):
        self._sections: list[Section] = []
        self._context: dict[str, Any] = {}

    def add(self, section: Section) -> PromptBuilder:
        self._sections.append(section)
        return self

    def remove(self, name: str) -> PromptBuilder:
        self._sections = [s for s in self._sections if s.name != name]
        return self

    def get_section(self, name: str) -> Section | None:
        for s in self._sections:
            if s.name == name:
                return s
        return None

    def enable(self, name: str) -> PromptBuilder:
        for s in self._sections:
            if s.name == name:
                s.enabled = True
        return self

    def disable(self, name: str) -> PromptBuilder:
        for s in self._sections:
            if s.name == name:
                s.enabled = False
        return self

    def context(self, **kwargs: Any) -> PromptBuilder:
        self._context.update(kwargs)
        return self

    def build(self) -> str:
        sorted_sections = sorted(
            self._sections,
            key=lambda s: (s.priority, s.name),
        )
        parts = [s.render(self._context) for s in sorted_sections if s.enabled]
        return "\n\n".join(p for p in parts if p)


# === Built-in renderers ===

def _ensure_memory_dir() -> None:
    from ...paths import MEMORY_DIR

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
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
    for filename, content in defaults.items():
        path = MEMORY_DIR / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _read_memory_file(filename: str) -> str | None:
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


def _render_tools(ctx: dict[str, Any]) -> str:
    from ...tools.base import ToolRegistry

    tools = ToolRegistry.all()
    if not tools:
        return ""
    lines = ["You have access to the following tools:"]
    for tool in tools:
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


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


# === Factory functions ===

def default_prompt() -> PromptBuilder:
    """Create the default prompt builder"""
    return (PromptBuilder()
        .add(Section(name="soul", priority=10, render=_render_soul))
        .add(Section(name="user", priority=11, render=_render_user))
        .add(Section(name="memory", priority=12, render=_render_memory))
        .add(Section(name="environment", priority=20, render=_render_environment))
        .add(Section(name="tools", priority=30, render=_render_tools))
        .add(Section(name="skills", priority=40, render=_render_skills)))


def minimal_prompt() -> PromptBuilder:
    """Create a minimal prompt builder (identity + environment + tools)"""
    return (PromptBuilder()
        .add(Section(name="soul", priority=10, render=_render_soul))
        .add(Section(name="environment", priority=20, render=_render_environment))
        .add(Section(name="tools", priority=30, render=_render_tools)))
