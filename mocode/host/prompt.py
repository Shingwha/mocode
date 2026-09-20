"""System prompt assembly — plugin-contributed sections, rendered as XML.

What a prompt *says* is a capability and arrives from plugins through
``ctx.prompt_sections``; this module owns only the mechanism: rendering
sections in ``(priority, insertion order)`` order, so stable content stays in
front of volatile content and the provider's prefix cache stays warm.

What happens to a prompt after it is built — freezing it for a session,
noticing when the world moved on — lives one layer out: the freeze and its
reinstatement in :class:`~mocode.host.conversation.Conversation`, and the
notices in the cache-protect plugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
