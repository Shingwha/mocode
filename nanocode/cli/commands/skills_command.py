"""/skills 命令 - 列出和选择 skills"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error, format_success
from ...skills.manager import SkillManager


@command("/skills", description="List and select skills")
class SkillsCommand(Command):
    """列出和选择可用的 skills"""

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
            # 交互式选择
            skill_name = self._select_interactive(skills, manager)
            if not skill_name:
                return True
        elif arg.isdigit():
            # 数字选择
            num = int(arg)
            if 1 <= num <= len(skills):
                skill_name = skills[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            # 直接指定名称
            if arg in skills:
                skill_name = arg
            else:
                error(f"Skill not found: {arg}")
                return True

        # 加载 skill 内容
        skill = manager.get_skill(skill_name)
        if not skill:
            error(f"Skill not found: {skill_name}")
            return True

        content = skill.load_content()

        # 显示激活信息（只显示名字）
        ctx.layout.add_command_output(format_success(f"Activated skill: {skill_name}"))

        # 设置待发送消息
        ctx.pending_message = f"/{skill_name}\n\n{content}"

        return True

    def _select_interactive(self, skills: list[str], manager: SkillManager) -> str | None:
        """交互式选择 skill"""
        choices = []
        for name in sorted(skills):
            skill = manager.get_skill(name)
            if skill:
                # 截断描述以便显示
                desc = skill.metadata.description
                if len(desc) > 50:
                    desc = desc[:50] + "..."
                display = f"{name} - {desc}"
                choices.append((name, display))

        menu = SelectMenu("Select a skill to activate", choices)
        return menu.show()
