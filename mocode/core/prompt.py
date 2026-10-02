"""Prompt building — section-based, XML output.

Sections are rendered in ``(priority, insertion order)`` order, so a builder can
place stable content first and volatile content last to keep the provider's
prefix cache warm. Nested sections become nested XML tags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Self
from xml.sax.saxutils import quoteattr

#: A section name becomes an XML tag, so it must be a valid one. Names come
#: from code, not from users — an invalid name is a bug worth failing on.
_XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _xml_tag(tag: str, content: str = "", **attrs: str) -> str:
    if not _XML_NAME.match(tag):
        raise ValueError(
            f"section name {tag!r} is not a valid XML tag name "
            "(letters, digits, '_', '-', '.', not starting with a digit)"
        )
    attr_str = "".join(f" {k}={quoteattr(str(v))}" for k, v in attrs.items())
    if not content:
        return f"<{tag}{attr_str}></{tag}>"
    return f"<{tag}{attr_str}>\n{content}\n</{tag}>"


Content = str | list["Section"] | Callable[[dict[str, Any]], str]


@dataclass
class Section:
    """One piece of a prompt.

    ``content`` is static text, nested sections, or a callable receiving the
    builder's context; ``render`` is the same callable idea as a first-class
    field — a section that renders itself, which is the shape a section
    derived from live state wants (see ``pinned`` and ``derived_from``).

    ``pinned=True`` freezes the section's *rendered* bytes after the first
    :meth:`Prompt.build <Prompt.build>` — a section that re-renders from a
    live registry on every build would fight the frozen prompt around it.
    The cache survives until :meth:`refresh`, which is what a deliberate
    re-render (the host's rebuild) calls.

    ``derived_from`` declares lineage — ``"tools"`` says "my text is derived
    from the tool registry" — so a watcher that diffs the source can also
    diff the section. It is metadata: nothing in this module reads it.
    """

    name: str
    content: Content | None = None
    priority: int = field(kw_only=True, default=0)
    enabled: bool = field(kw_only=True, default=True)
    attrs: dict[str, str] = field(kw_only=True, default_factory=dict)
    render: Callable[[dict[str, Any]], str] | None = field(kw_only=True, default=None)
    pinned: bool = field(kw_only=True, default=False)
    derived_from: str | None = field(kw_only=True, default=None)
    #: The render-once cache for pinned sections — written by ``Prompt.build``,
    #: dropped by :meth:`refresh`.
    _pinned_text: "str | None" = field(default=None, init=False, repr=False, compare=False)
    _pinned_ready: bool = field(default=False, init=False, repr=False, compare=False)

    def refresh(self) -> None:
        """Drop the pinned render cache — the next build re-renders this
        section. The deliberate invalidation: a rebuild wants fresh content
        even from sections pinned for cache stability."""
        self._pinned_text = None
        self._pinned_ready = False


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
        """Add *section*, replacing one of the same name — last one wins.

        Returns ``self`` so registrations chain. A new section starts enabled.
        """
        self._sections[section.name] = section
        return self

    def unregister(self, name: str) -> Section | None:
        """Remove *name*, returning the section that was there (``None`` if none)."""
        return self._sections.pop(name, None)

    def get(self, name: str) -> Section | None:
        """The section named *name* — registered or not, enabled or not."""
        return self._sections.get(name)

    def all(self) -> list[Section]:
        """Every registered section — the management view, enabled or not."""
        return list(self._sections.values())

    def names(self) -> list[str]:
        """Names of the enabled sections — what ``build()`` would render."""
        return [s.name for s in self._sections.values() if s.enabled]

    def enable(self, name: str) -> Self:
        """Render *name* again. An unknown name is ignored, not an error."""
        s = self.get(name)
        if s:
            s.enabled = True
        return self

    def disable(self, name: str) -> Self:
        """Stop rendering *name*. It stays registered and ``get()``-able."""
        s = self.get(name)
        if s:
            s.enabled = False
        return self

    def render(self, section: Section) -> str | None:
        """One section rendered as its XML tag — the **live** render, never
        the pin cache.

        ``None`` when it renders to nothing — the caller skips it the same
        way ``build`` does. Pinning is a ``build()`` behaviour (a frozen
        prompt needs stable bytes); a caller asking "what would this section
        say now" — a diff, an inspection — wants the truth of the moment.
        """
        content = self._render(section)
        if not content:
            return None
        return _xml_tag(section.name, content, **section.attrs)

    def _render(self, section: Section) -> str | None:
        content: Content | None = section.content
        if section.render is not None:
            content = section.render(self._context)
        elif callable(content):
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
        """Render all enabled sections into one XML string.

        A ``pinned`` section contributes its cached render here — rendered
        once, then byte-identical until the section is :meth:`refreshed
        <Section.refresh>` — so a section drawing from a live registry cannot
        churn the prompt around it.
        """
        ordered = sorted(
            enumerate(self._sections.values()),
            key=lambda pair: (pair[1].priority, pair[0]),
        )
        parts = []
        for _, s in ordered:
            if not s.enabled:
                continue
            if s.pinned:
                if not s._pinned_ready:
                    s._pinned_text = self._render(s)
                    s._pinned_ready = True
                content = s._pinned_text
            else:
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
