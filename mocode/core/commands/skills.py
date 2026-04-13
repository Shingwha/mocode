"""/skills command - List, select, install, and manage skills"""

from .base import Command, CommandContext, command
from .result import CommandResult
from .utils import resolve_selection
from ...skills.manager import SkillManager
from ...skills.installer import SkillInstaller
from ...core.installer import SourceType as SkillSourceType


@command("/skills", description="List and select skills")
class SkillsCommand(Command):
    """List and select available skills"""

    _SUBCOMMANDS = {
        "install": "_install",
        "uninstall": "_uninstall",
        "update": "_update",
    }

    def __init__(self):
        self._installer = None

    def _get_installer(self) -> SkillInstaller:
        if self._installer is None:
            self._installer = SkillInstaller()
        return self._installer

    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        # Route subcommands
        result = self._route_subcommand(ctx, arg, self._SUBCOMMANDS)
        if result is not None:
            return result

        # Default: list and select skills
        manager = SkillManager.get_instance()
        skills = manager.list_skills()

        if not skills:
            dirs_info = [
                {"path": str(d), "exists": d.exists()}
                for d in manager.skills_dirs
            ]
            return CommandResult(
                message="No skills available.",
                data={"skills_dirs": dirs_info},
            )

        skill_name = resolve_selection(arg, sorted(skills))
        if not skill_name or skill_name not in skills:
            if skill_name:
                return CommandResult(success=False, message=f"Skill not found: {skill_name}")
            # No arg - return skills list for interactive selection
            skills_info = []
            for name in sorted(skills):
                skill = manager.get_skill(name)
                desc = skill.metadata.description if skill else ""
                skills_info.append({"name": name, "description": desc})
            return CommandResult(data={"skills": skills_info})

        skill = manager.get_skill(skill_name)
        if not skill:
            return CommandResult(success=False, message=f"Skill not found: {skill_name}")

        content = skill.load_content()
        return CommandResult(
            success=True,
            message=f"Activated skill: {skill_name}",
            data={"type": "skill_activated", "skill_name": skill_name, "content": content},
        )

    # --- Subcommand handlers ---

    def _install(self, ctx: CommandContext, url: str) -> CommandResult:
        if not url:
            return CommandResult(success=False, message="Usage: /skills install <github-url>")

        installer = self._get_installer()

        try:
            source_type, candidates = installer.discover_from_repo(url)

            if not candidates:
                return CommandResult(
                    success=False,
                    message="No valid skill found in repository. A skill needs a SKILL.md file.",
                )

            if source_type == SkillSourceType.SINGLE:
                candidate = candidates[0]
                result = installer.install(url, candidate=candidate)

                if result.success:
                    if result.already_installed:
                        return CommandResult(
                            message=f"Skill '{result.item_name}' is already installed"
                        )
                    self._refresh_skills()
                    return CommandResult(
                        success=True,
                        message=f"Skill '{result.item_name}' installed successfully",
                    )
                return CommandResult(success=False, message=result.error)

            # Multi-skill repo - return candidates for interactive selection
            return CommandResult(data={
                "action": "install_multi",
                "candidates": [
                    {"name": c.name, "description": c.description}
                    for c in candidates
                ],
                "url": url,
            })

        except ValueError as e:
            return CommandResult(success=False, message=str(e))
        except Exception as e:
            return CommandResult(success=False, message=f"Installation failed: {e}")

    def _uninstall(self, ctx: CommandContext, name: str) -> CommandResult:
        if not name:
            return CommandResult(success=False, message="Usage: /skills uninstall <name>")

        installer = self._get_installer()

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            return CommandResult(
                success=False,
                message=f"Skill '{name}' was not installed via /skills install. "
                        "You can manually remove it from ~/.mocode/skills/",
            )

        manager = SkillManager.get_instance()
        if name not in manager.list_skills():
            return CommandResult(success=False, message=f"Skill '{name}' not found")

        if installer.uninstall(name):
            self._refresh_skills()
            return CommandResult(success=True, message=f"Skill '{name}' uninstalled")
        return CommandResult(success=False, message=f"Failed to uninstall '{name}'")

    def _update(self, ctx: CommandContext, name: str) -> CommandResult:
        if not name:
            return CommandResult(success=False, message="Usage: /skills update <name>")

        installer = self._get_installer()

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            return CommandResult(
                success=False,
                message=f"Skill '{name}' was not installed via /skills install",
            )

        result = installer.update(name)

        if result.success:
            self._refresh_skills()
            return CommandResult(success=True, message=f"Skill '{name}' updated successfully")
        return CommandResult(success=False, message=result.error)

    # --- Helpers ---

    def _refresh_skills(self) -> None:
        """Refresh SkillManager to pick up new/removed skills."""
        manager = SkillManager.get_instance()
        manager.refresh()
