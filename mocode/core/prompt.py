"""Prompt building — section-based, XML output.

Sections are rendered in ``(priority, insertion order)`` order, so a builder can
place stable content first and volatile content last to keep the provider's
prefix cache warm. Nested sections become nested XML tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Self


def _xml_tag(tag: str, content: str = "", **attrs: str) -> str:
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
    if not content:
        return f"<{tag}{attr_str}></{tag}>"
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


Content = str | list["Section"] | Callable[[dict[str, Any]], str]


@dataclass
class Section:
    name: str
    content: Content
    priority: int = 0
    enabled: bool = True
    attrs: dict[str, str] = field(default_factory=dict)


class Prompt:
    """Section-based prompt builder — the container surface ToolRegistry uses.

    ``all()`` is the management view (every section); ``names()`` is what
    ``build()`` would render (enabled sections only). A disabled section stays
    registered and ``get()``-able, it just does not render — the same toggle
    semantics :class:`~mocode.core.tool.ToolRegistry` gives a tool.
    """

    def __init__(self, sections: list[Section] | None = None) -> None:
        self._sections: dict[str, Section] = {}
        self._context: dict[str, Any] = {}
        if sections:
            for s in sections:
                self._sections[s.name] = s

    def register(self, section: Section) -> Self:
        self._sections[section.name] = section
        return self

    def unregister(self, name: str) -> Section | None:
        return self._sections.pop(name, None)

    def get(self, name: str) -> Section | None:
        return self._sections.get(name)

    def all(self) -> list[Section]:
        return list(self._sections.values())

    def names(self) -> list[str]:
        """Names of the enabled sections — what ``build()`` would render."""
        return [s.name for s in self._sections.values() if s.enabled]

    def enable(self, name: str) -> Self:
        s = self.get(name)
        if s:
            s.enabled = True
        return self

    def disable(self, name: str) -> Self:
        s = self.get(name)
        if s:
            s.enabled = False
        return self

    def context(self, **kwargs: Any) -> Self:
        self._context.update(kwargs)
        return self

    def _render(self, section: Section) -> str | None:
        content = section.content
        if callable(content):
            content = content(self._context)

        if isinstance(content, list):
            parts = []
            for child in content:
                if not child.enabled:
                    continue
                child_content = self._render(child)
                if not child_content:
                    continue
                parts.append(_xml_tag(child.name, child_content, **child.attrs))
            return "\n\n".join(parts) if parts else None
        return content if content else None

    def build(self, wrap: str = "system-prompt") -> str:
        """Render all enabled sections into one XML string."""
        ordered = sorted(
            enumerate(self._sections.values()),
            key=lambda pair: (pair[1].priority, pair[0]),
        )
        parts = []
        for _, s in ordered:
            if not s.enabled:
                continue
            content = self._render(s)
            if not content:
                continue
            parts.append(_xml_tag(s.name, content, **s.attrs))
        body = "\n\n".join(parts)
        return f"<{wrap}>\n\n{body}\n\n</{wrap}>"

    def __len__(self) -> int:
        return len(self._sections)

    def __contains__(self, name: str) -> bool:
        return name in self._sections

    def __repr__(self) -> str:
        sections = ", ".join(
            f"{s.name}({'on' if s.enabled else 'off'})" for s in self._sections.values()
        )
        return f"Prompt([{sections}])"
