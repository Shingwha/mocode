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
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.prompt import Prompt, Section

if TYPE_CHECKING:
    from .plugin.context import HostContext


def build_system_prompt(ctx: HostContext) -> str:
    """Render the main agent system prompt as an XML string."""
    prompt = Prompt()
    for section in _framework_sections(ctx):
        prompt.register(section)
    for section in ctx.prompt_sections:
        if prompt.get(section.name) is None:  # framework sections win
            prompt.register(section)
    return prompt.build()


def _framework_sections(ctx: HostContext) -> list[Section]:
    # Order: stable → volatile (maximises prefix cache hits).
    return [
        Section("guidelines", _GUIDELINES, priority=10),
        Section("agents", _render_agents(ctx.home, ctx.cwd), priority=20),
        Section("environment", _render_environment(ctx), priority=30),
        Section("tools", _render_tools(ctx), priority=40),
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
    ]
    return "\n".join(parts)


def _render_tools(ctx: HostContext) -> list[Section]:
    return [Section("tool", t.description, attrs={"name": t.name}) for t in ctx.tools.all()]
