"""/skills command - List and select skills"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, MenuAction, MenuItem, is_cancelled, error, format_success
from ...skills.manager import SkillManager


@command("/skills", description="List and select skills")
class SkillsCommand(Command):
    """List and select available skills"""

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        manager = SkillManager.get_instance()
        skills = manager.list_skills()

        if not skills:
            ctx.layout.add_command_output("No skills available.")
            ctx.layout.add_command_output("")
            ctx.layout.add_command_output("Skills directories:")
            for d in manager.skills_dirs:
                status = "(exists)" if d.exists() else "(not found)"
                ctx.layout.add_command_output(f"  {d} {status}")
            return True

        if not arg:
            skill_name = self._select_interactive(skills, manager)
            if not skill_name:
                return True
        elif arg.isdigit():
            num = int(arg)
            if 1 <= num <= len(skills):
                skill_name = skills[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            if arg in skills:
                skill_name = arg
            else:
                error(f"Skill not found: {arg}")
                return True

        skill = manager.get_skill(skill_name)
        if not skill:
            error(f"Skill not found: {skill_name}")
            return True

        content = skill.load_content()
        ctx.layout.add_command_output(format_success(f"Activated skill: {skill_name}"))
        ctx.pending_message = f"/{skill_name}\n\n{content}"

        return True

    def _select_interactive(self, skills: list[str], manager: SkillManager) -> str | None:
        """Interactively select a skill."""
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

        selected = SelectMenu("Select a skill to activate", choices).show()
        return None if is_cancelled(selected) else selected
