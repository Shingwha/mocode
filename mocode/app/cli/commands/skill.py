"""Skill commands — auto-registered per skill as /skill:<name>."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import CommandContext, CommandResult

if TYPE_CHECKING:
    from ....core.skill import Skill


def make_skill_command(skill: Skill):
    skill_name = skill.metadata.name
    skill_desc = skill.metadata.description

    class _SkillCommand:
        name = f"/skill:{skill_name}"
        description = skill_desc
        aliases: tuple[str, ...] = ()

        async def run(self, ctx: CommandContext) -> CommandResult:
            content = skill.load_content()
            if not content:
                ctx.display.warn(f"Skill '{skill_name}' has no content.")
                return CommandResult.CONTINUE

            prompt = f"[Skill:{skill_name} — instructions below, do NOT call the skill tool]\n\n{content}"
            if ctx.args:
                prompt += f"\n\n---\n\nUser request: {ctx.args}"
            return CommandResult.text(prompt)

    return _SkillCommand()
