"""Skill commands — auto-registered per skill as /skill:<name>."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import Command, CommandContext, CommandResult

if TYPE_CHECKING:
    from ....core.skill import Skill


def make_skill_command(skill: Skill) -> Command:
    """Create a Command for a skill."""
    skill_name = skill.metadata.name
    skill_desc = skill.metadata.description

    async def _handle(ctx: CommandContext) -> CommandResult:
        content = skill.load_content()
        if not content:
            ctx.display.warn(f"Skill '{skill_name}' has no content.")
            return CommandResult.CONTINUE

        prompt = f"[Skill:{skill_name} — instructions below, do NOT call the skill tool]\n\n{content}"
        if ctx.args:
            prompt += f"\n\n---\n\nUser request: {ctx.args}"
        return CommandResult.text(prompt)

    return Command(
        name=f"/skill:{skill_name}",
        description=skill_desc,
        handler=_handle,
    )
