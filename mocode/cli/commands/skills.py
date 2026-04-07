"""/skills command - List, select, install, and manage skills"""

from .base import Command, CommandContext, command
from .utils import resolve_selection
from ..ui.components import MultiSelect
from ..ui.prompt import confirm
from ..ui.styles import DIM, RESET
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

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        # Route subcommands
        result = self._route_subcommand(ctx, arg, self._SUBCOMMANDS)
        if result is not None:
            return result

        # Default: list and select skills
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

    # --- Subcommand handlers ---

    def _install(self, ctx: CommandContext, url: str) -> bool:
        if not url:
            self._error(ctx, "Usage: /skills install <github-url>")
            return True

        installer = self._get_installer()

        try:
            source_type, candidates = installer.discover_from_repo(url)

            if not candidates:
                self._error(ctx, "No valid skill found in repository. A skill needs a SKILL.md file.")
                return True

            if source_type == SkillSourceType.SINGLE:
                candidate = candidates[0]
                self._info(ctx, f"Installing {candidate.name}...")
                result = installer.install(url, candidate=candidate)

                if result.success:
                    if result.already_installed:
                        self._info(ctx, f"Skill '{result.item_name}' is already installed")
                    else:
                        self._success(ctx, f"Skill '{result.item_name}' installed successfully")
                        self._refresh_skills()
                else:
                    self._error(ctx, result.error)
                return True

            # Multi-skill repo
            self._info(ctx, f"Found {len(candidates)} skills in repository")
            choices = [
                (c.name, f"{c.name} - {c.description}" if c.description else c.name)
                for c in candidates
            ]

            multi = MultiSelect("Select skills to install", choices, min_selections=1)
            selected = multi.show()

            if not selected:
                self._info(ctx, "Installation cancelled")
                return True

            selected_candidates = [c for c in candidates if c.name in selected]
            results = installer.install_multiple(url, selected_candidates)

            success_count = sum(1 for r in results if r.success)
            for r in results:
                if r.success:
                    self._success(ctx, f"  + {r.item_name}")
                else:
                    self._error(ctx, f"  - {r.item_name}: {r.error}")

            if success_count > 0:
                self._refresh_skills()
                self._success(ctx, f"Installed {success_count} skill(s)")

            return True

        except ValueError as e:
            self._error(ctx, str(e))
            return True
        except Exception as e:
            self._error(ctx, f"Installation failed: {e}")
            return True

    def _uninstall(self, ctx: CommandContext, name: str) -> bool:
        if not name:
            self._error(ctx, "Usage: /skills uninstall <name>")
            return True

        installer = self._get_installer()

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            self._error(ctx, f"Skill '{name}' was not installed via /skills install")
            self._info(ctx, "You can manually remove it from ~/.mocode/skills/")
            return True

        manager = SkillManager.get_instance()
        if name not in manager.list_skills():
            self._error(ctx, f"Skill '{name}' not found")
            return True

        if not confirm(f"Uninstall skill '{name}'?"):
            self._info(ctx, "Cancelled")
            return True

        if installer.uninstall(name):
            self._refresh_skills()
            self._success(ctx, f"Skill '{name}' uninstalled")
        else:
            self._error(ctx, f"Failed to uninstall '{name}'")

        return True

    def _update(self, ctx: CommandContext, name: str) -> bool:
        if not name:
            self._error(ctx, "Usage: /skills update <name>")
            return True

        installer = self._get_installer()

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            self._error(ctx, f"Skill '{name}' was not installed via /skills install")
            return True

        self._info(ctx, f"Updating {name}...")
        result = installer.update(name)

        if result.success:
            self._refresh_skills()
            self._success(ctx, f"Skill '{name}' updated successfully")
        else:
            self._error(ctx, result.error)

        return True

    # --- Helpers ---

    def _refresh_skills(self) -> None:
        """Refresh SkillManager to pick up new/removed skills."""
        manager = SkillManager.get_instance()
        manager.refresh()

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
