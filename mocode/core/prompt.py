"""Prompt building — section-based, format-aware.

v0.3: Core only — no built-in renderers, no system_prompt() factory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Self


def _xml_tag(tag: str, content: str = "", **attrs: str) -> str:
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
    if not content:
        return f"<{tag}{attr_str}></{tag}>"
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


Content = str | list["Section"] | Callable[[dict[str, Any]], str]

Format = Literal["text", "xml"]


@dataclass(slots=True)
class Section:
    name: str
    content: Content
    priority: int = 0
    enabled: bool = True
    attrs: dict[str, str] = field(default_factory=dict)


class Prompt:
    """Section-based prompt builder — dict-backed, API aligned with ToolRegistry."""

    def __init__(self, sections: list[Section] | None = None) -> None:
        self._sections: dict[str, Section] = {}
        self._context: dict[str, Any] = {}
        if sections:
            for s in sections:
                self._sections[s.name] = s

    def add(self, section: Section) -> Self:
        self._sections[section.name] = section
        return self

    def remove(self, name: str) -> Section | None:
        return self._sections.pop(name, None)

    def find(self, name: str) -> Section | None:
        return self._sections.get(name)

    def all(self) -> list[Section]:
        return list(self._sections.values())

    def enable(self, name: str) -> Self:
        s = self.find(name)
        if s:
            s.enabled = True
        return self

    def disable(self, name: str) -> Self:
        s = self.find(name)
        if s:
            s.enabled = False
        return self

    def context(self, **kwargs: Any) -> Self:
        self._context.update(kwargs)
        return self

    def _render_xml(self, section: Section) -> str | None:
        content = section.content
        if callable(content):
            content = content(self._context)

        if isinstance(content, list):
            parts = []
            for child in content:
                if not child.enabled:
                    continue
                child_content = self._render_xml(child)
                if not child_content:
                    continue
                parts.append(_xml_tag(child.name, child_content, **child.attrs))
            return "\n\n".join(parts) if parts else None
        return content if content else None

    def _render_text(self, section: Section, indent: int = 0) -> str | None:
        content = section.content
        if callable(content):
            content = content(self._context)

        prefix = "  " * indent
        name_part = section.name
        if section.attrs:
            attrs_str = ", ".join(f"{k}={v}" for k, v in section.attrs.items())
            name_part = f"{name_part} ({attrs_str})"

        if isinstance(content, list):
            lines = [f"{prefix}{name_part}:"]
            for child in content:
                if not child.enabled:
                    continue
                child_text = self._render_text(child, indent + 1)
                if child_text:
                    lines.append(child_text)
            return "\n".join(lines) if len(lines) > 1 else None

        if not content:
            return None
        if "\n" in content:
            return f"{prefix}{name_part}:\n{content}"
        return f"{prefix}{name_part}: {content}"

    def build(self, format: str = "text", wrap: str | None = None) -> str:
        render = self._render_xml if format == "xml" else self._render_text
        sorted_sections = sorted(self._sections.values(), key=lambda s: (s.priority, s.name))
        parts = []
        for s in sorted_sections:
            if not s.enabled:
                continue
            content = render(s)
            if not content:
                continue
            if format == "xml":
                parts.append(_xml_tag(s.name, content, **s.attrs))
            else:
                parts.append(content)
        body = "\n\n".join(parts)
        if format == "xml":
            tag = wrap or "system-prompt"
            return f"<{tag}>\n\n{body}\n\n</{tag}>"
        return body

    def __repr__(self) -> str:
        sections = ", ".join(
            f"{s.name}({'on' if s.enabled else 'off'})" for s in self._sections.values()
        )
        return f"Prompt([{sections}])"
