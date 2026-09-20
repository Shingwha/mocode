"""System prompt assembly — plugin-contributed sections, rendered and diffed.

What a prompt *says* is a capability and arrives from plugins through
``ctx.prompt_sections``; what this module owns is mechanism: rendering
sections as XML in ``(priority, insertion order)`` order so stable content
stays in front of volatile content (keeping the provider's prefix cache warm),
and noticing when a session's prompt no longer matches the world.

Freezing and drift
------------------
The prompt a session runs with is *frozen*: a resume reinstates it
byte-identical, so the provider's prefix cache for the old turns survives.
What changed since the model was last told is not written into the prompt —
it is appended to the history as a context-update notice, section by section
(:func:`diff_sections`), and the session remembers the last announced state
(``prompt_seen``) as the baseline for the next diff: what the model was last
told is what new drift is measured against. ``Conversation.rebuild_prompt``
re-freezes outright, accepting the cache loss that follows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.prompt import Prompt

if TYPE_CHECKING:
    from .core.prompt import Section
    from .plugin.context import HostContext


def build_system_prompt(ctx: HostContext) -> str:
    """Render the main agent system prompt as an XML string."""
    prompt = Prompt()
    for section in current_sections(ctx):
        prompt.register(section)
    return prompt.build()


def current_sections(ctx: HostContext) -> list[Section]:
    """The sections a prompt would render now, stable → volatile.

    Every section is a plugin contribution — the host contributes no prompt
    content of its own. A name collision resolves by registration order: the
    last one registered wins, the same rule every other contribution follows.
    """
    return sorted(ctx.prompt_sections, key=lambda section: section.priority)


def rendered_sections(ctx: HostContext) -> list[dict[str, Any]]:
    """What ``build_system_prompt`` would render, one record per section.

    ``{"name", "attrs", "xml"}`` — comparable across time and serializable
    into a session, which is how drift is noticed on resume.
    """
    prompt = Prompt()
    records = []
    for section in current_sections(ctx):
        prompt.register(section)
        xml = prompt.render(section)
        if xml is not None:
            records.append({"name": section.name, "attrs": section.attrs, "xml": xml})
    return records


#: How much of a changed section a notice carries — an AGENTS.md-sized edit
#: must not swamp the history.
_DIFF_LIMIT = 2000


def diff_sections(
    seen: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[str]:
    """What changed between two :func:`rendered_sections` snapshots.

    One entry per section — added, removed, or now reading differently —
    carrying the new content only: the prompt the model still runs with is
    already in its context, so a notice states the current truth and nothing
    else. The baseline is what the model was last told; a change reverted
    before it was ever announced is not a change.
    """
    old = {record["name"]: record["xml"] for record in seen}
    new = {record["name"]: record["xml"] for record in fresh}
    lines = []
    for record in fresh:
        name, xml = record["name"], record["xml"]
        if name not in old:
            lines.append(f'- section "{name}" was added:\n{_clip(xml)}')
        elif old[name] != xml:
            lines.append(f'- section "{name}" now reads:\n{_clip(xml)}')
    for name in old:
        if name not in new:
            lines.append(f'- section "{name}" was removed')
    return lines


def drift_notice(changes: list[str]) -> str:
    """The message appended to the history: the context moved, here is how."""
    return "[context update — the environment changed since the system prompt was written]\n" + "\n".join(
        changes
    )


def _clip(text: str) -> str:
    if len(text) <= _DIFF_LIMIT:
        return text
    return text[:_DIFF_LIMIT] + f"\n… ({len(text) - _DIFF_LIMIT} more characters omitted)"
