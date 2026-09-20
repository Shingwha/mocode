"""System prompt assembly — framework sections plus plugin contributions.

Sections are rendered in ``(priority, insertion order)`` order so a plugin can
place stable content before volatile content, keeping the provider's prefix
cache warm.

Framework sections reflect host state and always win a name collision; plugins
contribute through ``ctx.prompt_sections``.

AGENTS.md
---------
Two locations are read and merged (global first, then project):

  - ``~/.mocode/AGENTS.md``   user-level instructions for every project
  - ``./AGENTS.md``           project-level instructions

Both are optional. If neither exists, a short hint is rendered instead.

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

import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.prompt import Prompt, Section

if TYPE_CHECKING:
    from .plugin.context import HostContext


def build_system_prompt(ctx: HostContext) -> str:
    """Render the main agent system prompt as an XML string."""
    prompt = Prompt()
    for section in current_sections(ctx):
        prompt.register(section)
    return prompt.build()


def current_sections(ctx: HostContext) -> list[Section]:
    """The sections a prompt would render now: framework first, plugin
    contributions where they do not collide, ordered stable → volatile."""
    sections = _framework_sections(ctx)
    known = {section.name for section in sections}
    for section in ctx.prompt_sections:
        if section.name not in known:  # framework sections win
            sections.append(section)
            known.add(section.name)
    return sorted(sections, key=lambda section: section.priority)


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


def _framework_sections(ctx: HostContext) -> list[Section]:
    # Order: stable → volatile (maximises prefix cache hits).
    return [
        Section("guidelines", _GUIDELINES, priority=10),
        Section("agents", _render_agents(ctx.home, ctx.cwd), priority=20),
        Section("environment", _render_environment(ctx), priority=30),
        Section("tools", _render_tools(ctx), priority=40),
        Section("time", _render_time(), priority=60),
    ]


_GUIDELINES = "\n".join(
    [
        "- Be concise and direct",
        "- Verify changes before claiming success",
        "- Handle errors gracefully",
        "- Ask before destructive or irreversible operations",
    ]
)


def _render_agents(home: Path, cwd: Path) -> list[Section]:
    """Read AGENTS.md files and render the agents section."""
    header = (
        "The following instructions are loaded from AGENTS.md files — a place for "
        "project-specific and user-specific guidance that helps you work effectively. "
        "Treat them as rules from the project owner: follow build steps, respect code "
        "conventions, and heed any warnings listed below."
    )

    agent_sections = []
    for label, path in (("global", home / "AGENTS.md"), ("project", cwd / "AGENTS.md")):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if content:
            agent_sections.append(
                Section("agent", content, attrs={"source": label, "path": str(path)})
            )

    if not agent_sections:
        hint = (
            "No AGENTS.md files found yet. You can create them to provide persistent "
            "instructions. Common sections: project overview, build/test commands, code style, "
            "testing instructions, security considerations."
        )
        agent_sections.append(Section("agent", hint, attrs={"source": "hint"}))

    return [Section("header", header)] + agent_sections


def _render_environment(ctx: HostContext) -> str:
    parts = [
        f"cwd: {ctx.cwd}",
        f"home: {ctx.home}",
        f"config: {ctx.home / 'config.json'}",
        f"os: {platform.system()} {platform.release()}",
    ]
    return "\n".join(parts)


def _render_time() -> str:
    """The one line that goes stale — which is why it renders last, and why
    a resume corrects it with a notice instead of a new prompt."""
    return f"today: {datetime.now():%Y-%m-%d (%A)}"


def _render_tools(ctx: HostContext) -> list[Section]:
    return [Section("tool", t.description, attrs={"name": t.name}) for t in ctx.tools.all()]
