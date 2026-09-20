"""default-prompts plugin — the sections a fresh conversation starts with.

What a system prompt *says* is a capability, so the host contributes none of
it: ``host/prompt.py`` renders and diffs sections, and these four are the ones
MoCode ships. They are ordinary contributions — the same ``Section`` objects a
third party appends to ``ctx.prompt_sections`` — so they can be turned off
with ``plugins.default-prompts.enabled = false`` and replaced by any plugin
that registers the same names.

Order is stable → volatile so the provider's prefix cache stays warm:

  - ``guidelines``    how to work, statically
  - ``agents``        AGENTS.md instructions
  - ``environment``   where the conversation is
  - ``time``          the one line that goes stale

AGENTS.md
---------
Two locations are read and merged (global first, then project):

  - ``~/.mocode/AGENTS.md``   user-level instructions for every project
  - ``./AGENTS.md``           project-level instructions

Both are optional. If neither exists, a short hint is rendered instead.

``agents`` and ``time`` are callable sections: a re-render
(``conversation.rebuild_prompt()``) re-reads the files and the clock, so a
long-lived session's prompt is corrected by a notice instead of quietly
going stale.
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ....core.prompt import Section
from ..base import Plugin
from ..context import HostContext

_GUIDELINES = "\n".join(
    [
        "- Be concise and direct",
        "- Verify changes before claiming success",
        "- Handle errors gracefully",
        "- Ask before destructive or irreversible operations",
    ]
)


def _agents(home: Path, cwd: Path) -> Callable[[dict[str, Any]], list[Section]]:
    """Callable content for the agents section — the files are re-read on
    every render, which is what makes a rebuild see an edited AGENTS.md."""

    def render(_ctx: dict[str, Any]) -> list[Section]:
        header = (
            "The following instructions are loaded from AGENTS.md files — a place for "
            "project-specific and user-specific guidance that helps you work effectively. "
            "Treat them as rules from the project owner: follow build steps, respect code "
            "conventions, and heed any warnings listed below."
        )

        agent_sections = []
        for label, path in (
            ("global", home / "AGENTS.md"),
            ("project", cwd / "AGENTS.md"),
        ):
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

    return render


def _environment(ctx: HostContext) -> str:
    parts = [
        f"cwd: {ctx.cwd}",
        f"home: {ctx.home}",
        f"config: {ctx.home / 'config.json'}",
        f"os: {platform.system()} {platform.release()}",
    ]
    return "\n".join(parts)


def _render_time(_ctx: dict[str, Any]) -> str:
    """The one line that goes stale — which is why it renders last, and why
    a resume corrects it with a notice instead of a new prompt."""
    return f"today: {datetime.now():%Y-%m-%d (%A)}"


class DefaultPromptsPlugin(Plugin):
    name = "default-prompts"
    description = (
        "The default prompt sections: guidelines, AGENTS.md, environment, time"
    )

    def build(self, ctx: HostContext) -> None:
        ctx.prompt_sections += [
            Section("guidelines", _GUIDELINES, priority=10),
            Section("agents", _agents(ctx.home, ctx.cwd), priority=20),
            Section("environment", _environment(ctx), priority=30),
            Section("time", _render_time, priority=60),
        ]


PLUGIN = DefaultPromptsPlugin()
