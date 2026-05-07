"""Prompt building — section-based, format-aware."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Self

from .paths import MEMORY_DIR, CONFIG_PATH, MOCODE_HOME, SESSIONS_DIR, SKILLS_DIR


# --- Shared utility ---


def xml_tag(tag: str, content: str = "", **attrs: str) -> str:
    """Construct an XML tag."""
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
    if not content:
        return f"<{tag}{attr_str}></{tag}>"
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


# --- Core types ---


@dataclass(slots=True)
class Section:
    name: str
    priority: int
    render: Callable[[dict[str, Any]], str]
    enabled: bool = True
    attrs: dict[str, str] = field(default_factory=dict)


Format = Literal["text", "xml"]


class PromptBuilder:
    """Section-based prompt builder with format control."""

    def __init__(self) -> None:
        self._sections: list[Section] = []
        self._context: dict[str, Any] = {}

    def add(self, section: Section) -> Self:
        self._sections.append(section)
        return self

    def remove(self, name: str) -> Self:
        self._sections = [s for s in self._sections if s.name != name]
        return self

    def section(self, name: str) -> Section | None:
        return next((s for s in self._sections if s.name == name), None)

    def enable(self, name: str) -> Self:
        s = self.section(name)
        if s:
            s.enabled = True
        return self

    def disable(self, name: str) -> Self:
        s = self.section(name)
        if s:
            s.enabled = False
        return self

    def context(self, **kwargs: Any) -> Self:
        self._context.update(kwargs)
        return self

    def build(self, format: Format = "text", wrap: str | None = None) -> str:
        sorted_sections = sorted(self._sections, key=lambda s: (s.priority, s.name))
        parts = []
        for s in sorted_sections:
            if not s.enabled:
                continue
            content = s.render(self._context)
            if not content:
                continue
            if format == "xml":
                parts.append(xml_tag(s.name, content, **s.attrs))
            else:
                parts.append(content)
        body = "\n\n".join(parts)
        if format == "xml":
            tag = wrap or "system-prompt"
            return f"<{tag}>\n\n{body}\n\n</{tag}>"
        return body


# --- Memory file helpers ---


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


def _read_memory(filename: str) -> str:
    _ensure_memory_dir()
    path = MEMORY_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


# --- Renderers (pure content, no formatting) ---


def _render_soul(ctx: dict[str, Any]) -> str:
    return _read_memory("SOUL.md")


def _render_user(ctx: dict[str, Any]) -> str:
    return _read_memory("USER.md")


def _render_memory(ctx: dict[str, Any]) -> str:
    return _read_memory("MEMORY.md")


def _render_environment(ctx: dict[str, Any]) -> str:
    cwd = ctx.get("cwd") or os.getcwd()
    return (
        f"cwd: {cwd}\n"
        f"home: {MOCODE_HOME}\n"
        f"config: {CONFIG_PATH}\n"
        f"skills: {SKILLS_DIR}\n"
        f"sessions: {SESSIONS_DIR}"
    )


def _render_tools(ctx: dict[str, Any]) -> str:
    tools = ctx.get("tools")
    if not tools or not tools.all():
        return ""
    return "\n".join(xml_tag(t.name, t.description) for t in tools.all())


def _render_skills(ctx: dict[str, Any]) -> str:
    sm = ctx.get("skill_manager")
    if not sm:
        return ""
    skills = sm.get_all_metadata()
    if not skills:
        return ""
    return "\n".join(xml_tag(s.name, s.description) for s in skills)


# --- Factory ---


def system_prompt() -> PromptBuilder:
    """Full system prompt with all sections."""
    return (PromptBuilder()
        .add(Section("soul", 10, _render_soul, attrs={"file": str(MEMORY_DIR / "SOUL.md")}))
        .add(Section("user", 11, _render_user, attrs={"file": str(MEMORY_DIR / "USER.md")}))
        .add(Section("memory", 12, _render_memory, attrs={"file": str(MEMORY_DIR / "MEMORY.md")}))
        .add(Section("environment", 20, _render_environment))
        .add(Section("tools", 30, _render_tools))
        .add(Section("skills", 40, _render_skills)))
