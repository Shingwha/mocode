"""PromptBuilder + Section + default_prompt

v0.2 改进：_render_tools 使用 ctx["tools"] (ToolRegistry 实例) 而非全局 ToolRegistry。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .paths import MEMORY_DIR, CONFIG_PATH, MOCODE_HOME, SESSIONS_DIR, SKILLS_DIR


@dataclass(slots=True)
class Section:
    name: str
    priority: int
    render: Callable[[dict[str, Any]], str]
    enabled: bool = True


@dataclass
class PromptContributions:
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
        body = "\n\n".join(p for p in parts if p)
        return f"<system-prompt>\n\n{body}\n\n</system-prompt>"


# === Built-in renderers ===


def _ensure_memory_dir() -> None:
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
    path = MEMORY_DIR / 'SOUL.md'
    return f'<soul file="{path}">\n{content}\n</soul>'


def _render_user(ctx: dict[str, Any]) -> str:
    content = _read_memory_file("USER.md")
    if not content:
        return ""
    path = MEMORY_DIR / 'USER.md'
    return f'<user file="{path}">\n{content}\n</user>'


def _render_memory(ctx: dict[str, Any]) -> str:
    content = _read_memory_file("MEMORY.md")
    if not content:
        return ""
    path = MEMORY_DIR / 'MEMORY.md'
    return f'<memory file="{path}">\n{content}\n</memory>'


def _render_environment(ctx: dict[str, Any]) -> str:
    cwd = ctx.get("cwd") or os.getcwd()
    return (
        f'<environment'
        f' cwd="{cwd}"'
        f' home="{MOCODE_HOME}"'
        f' config="{CONFIG_PATH}"'
        f' skills="{SKILLS_DIR}"'
        f' sessions="{SESSIONS_DIR}"'
        f'>\n</environment>'
    )


def _render_tools(ctx: dict[str, Any]) -> str:
    """使用 ctx['tools'] (ToolRegistry 实例) 而非全局"""
    tools = ctx.get("tools")
    if not tools:
        return ""
    all_tools = tools.all()
    if not all_tools:
        return ""
    lines = ["<tools>"]
    for tool in all_tools:
        lines.append(f"- {tool.name}: {tool.description}")
    lines.append("</tools>")
    return "\n".join(lines)


def _render_skills(ctx: dict[str, Any]) -> str:
    skill_manager = ctx.get("skill_manager")
    if not skill_manager:
        return ""
    skills = skill_manager.get_all_metadata()
    if not skills:
        return ""
    lines = ["<skills>"]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    lines.append("</skills>")
    return "\n".join(lines)


# === Factory functions ===


def default_prompt() -> PromptBuilder:
    return (PromptBuilder()
        .add(Section(name="soul", priority=10, render=_render_soul))
        .add(Section(name="user", priority=11, render=_render_user))
        .add(Section(name="memory", priority=12, render=_render_memory))
        .add(Section(name="environment", priority=20, render=_render_environment))
        .add(Section(name="tools", priority=30, render=_render_tools))
        .add(Section(name="skills", priority=40, render=_render_skills)))


def minimal_prompt() -> PromptBuilder:
    return (PromptBuilder()
        .add(Section(name="soul", priority=10, render=_render_soul))
        .add(Section(name="environment", priority=20, render=_render_environment))
        .add(Section(name="tools", priority=30, render=_render_tools)))
