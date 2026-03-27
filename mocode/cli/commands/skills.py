"""/skills command - List and select skills"""

from .base import Command, CommandContext, command
from .utils import parse_selection_arg
from ..ui.prompt import select, MenuItem, is_cancelled
from ..ui.styles import MessagePreset, format_preset
from ...skills.manager import SkillManager


@command("/skills", description="List and select skills")
class SkillsCommand(Command):
    """List and select available skills"""

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        manager = SkillManager.get_instance()
        skills = manager.list_skills()

        if not skills:
            if ctx.display:
                ctx.display.command_output("No skills available.\n")
                ctx.display.command_output("Skills directories:")
                for d in manager.skills_dirs:
                    status = "(exists)" if d.exists() else "(not found)"
                    ctx.display.command_output(f"  {d} {status}")
            return True

        if not arg:
            skill_name = self._select_interactive(skills, manager)
            if not skill_name:
                return True
        else:
            skill_name = parse_selection_arg(arg, sorted(skills))
            if skill_name is None:
                return True
            if skill_name not in skills:
                if ctx.display:
                    ctx.display.error(f"Skill not found: {skill_name}")
                return True

        skill = manager.get_skill(skill_name)
        if not skill:
            if ctx.display:
                ctx.display.error(f"Skill not found: {skill_name}")
            return True

        content = skill.load_content()
        if ctx.display:
            ctx.display.success(f"Activated skill: {skill_name}")
        ctx.pending_message = f"/{skill_name}\n\n{content}"

        return True

    def _select_interactive(self, skills: list[str], manager: SkillManager) -> str | None:
        choices = []
        for name in sorted(skills):
            skill = manager.get_skill(name)
            if skill:
                desc = skill.metadata.description
                if len(desc) > 50:
                    desc = desc[:50] + "..."
                display = f"{name} - {desc}"
                choices.append((name, display))
        choices.append(MenuItem.exit_())

        selected = select("Select a skill to activate", choices)
        return None if is_cancelled(selected) else selected
