"""/skills command - List and select skills"""

from .base import Command, CommandContext, command
from .utils import resolve_selection
from ...skills.manager import SkillManager


@command("/skills", description="List and select skills")
class SkillsCommand(Command):
    """List and select available skills"""

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        manager = SkillManager.get_instance()
        skills = manager.list_skills()

        if not skills:
            self._output(ctx, "No skills available.\n")
            self._output(ctx, "Skills directories:")
            for d in manager.skills_dirs:
                status = "(exists)" if d.exists() else "(not found)"
                self._output(ctx, f"  {d} {status}")
            return True

        skill_name = resolve_selection(arg, sorted(skills), lambda: self._select_interactive(skills, manager))
        if not skill_name or skill_name not in skills:
            if skill_name:
                self._error(ctx, f"Skill not found: {skill_name}")
            return True

        skill = manager.get_skill(skill_name)
        if not skill:
            self._error(ctx, f"Skill not found: {skill_name}")
            return True

        content = skill.load_content()
        self._success(ctx, f"Activated skill: {skill_name}")
        ctx.pending_message = f"/{skill_name}\n\n{content}"

        return True

    def _select_interactive(self, skills: list[str], manager: SkillManager) -> str | None:
        def formatter(name):
            skill = manager.get_skill(name)
            desc = skill.metadata.description if skill else ""
            if len(desc) > 50:
                desc = desc[:50] + "..."
            return (name, f"{name} - {desc}")

        return self._select_from_list(
            "Select a skill to activate",
            sorted(skills),
            formatter,
        )
