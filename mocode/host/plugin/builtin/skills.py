"""skills plugin — directory-based skills: reusable instructions loaded on demand.

A skill is a directory holding ``SKILL.md`` with YAML frontmatter (``name``,
``description``) and a body of instructions. Any files next to it are reference
material the agent can read through the normal filesystem tools.

Skills are discovered from three places:

  - ``~/.mocode/skills/``          user-level, every project
  - ``./.mocode/skills/``          project-level
  - ``<plugin>/skills/``           shipped inside a plugin, for anything installed

The last one is the Agent Plugins standard's portable component, which is why a
plugin directory's own skills travel with it wherever it is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ....core.prompt import Section
from ....core.tool import Tool, ToolError
from ...command import CONTINUE, Command, CommandContext, CommandResult
from ..base import Plugin
from ..context import HostContext

SKILL_MD = "SKILL.md"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``---`` YAML frontmatter from *text*.

    Returns ``(frontmatter_dict, body_text)``; no frontmatter → ``({}, text)``.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml

        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}, text
    return (fm if isinstance(fm, dict) else {}), parts[2].strip()


# ── Skill model ─────────────────────────────────────────────


@dataclass
class SkillMetadata:
    name: str
    description: str
    attrs: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SkillMetadata:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            attrs={k: v for k, v in data.items() if k not in ("name", "description")},
        )


@dataclass
class Skill:
    """A skill directory plus its (lazily loaded) instructions."""

    metadata: SkillMetadata
    path: Path | None = None
    _content: str | None = None

    @property
    def base_dir(self) -> str:
        """Directory holding the skill and its reference files."""
        return str(self.path) if self.path else ""

    def load_content(self) -> str:
        if self._content is None:
            self._content = _read_body(self.path)
        return self._content

    @classmethod
    def from_dir(cls, skill_dir: Path) -> Skill | None:
        """Build a Skill from a directory containing SKILL.md, or ``None``."""
        frontmatter, body = _read_skill_md(skill_dir)
        metadata = SkillMetadata.from_dict(frontmatter)
        if not metadata.name:
            return None
        return cls(metadata=metadata, path=skill_dir, _content=body)


def _read_skill_md(skill_dir: Path) -> tuple[dict, str]:
    try:
        text = (skill_dir / SKILL_MD).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    return parse_frontmatter(text)


def _read_body(skill_dir: Path | None) -> str:
    if skill_dir is None:
        return ""
    return _read_skill_md(skill_dir)[1]


class SkillManager:
    """Discovers skills from directories and holds programmatically registered ones."""

    def __init__(self, skill_dirs: list[Path] | None = None):
        self._dirs = list(skill_dirs or [])
        self._skills: dict[str, Skill] = {}
        self._registered: dict[str, Skill] = {}
        self.discover()

    def register(self, skill: Skill) -> None:
        """Register a skill in code. A discovered skill of the same name wins."""
        if skill.metadata.name and skill.metadata.name not in self._skills:
            self._registered[skill.metadata.name] = skill

    def discover(self) -> None:
        """Re-scan the skill directories. Registered skills are kept."""
        self._skills.clear()
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not (child.is_dir() and (child / SKILL_MD).is_file()):
                    continue
                skill = Skill.from_dir(child)
                if skill:
                    self._skills[skill.metadata.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name) or self._registered.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values()) + list(self._registered.values())

    def names(self) -> list[str]:
        return list(self._skills) + list(self._registered)


# ── Tool & command ──────────────────────────────────────────

_SKILL_PARAMS = {"name": {"type": "string", "description": "The skill name to load"}}
_SKILL_DESC = (
    "Load a skill by name. Use when the user's request matches a skill's description. "
    "Returns the skill's instructions for you to follow."
)


class SkillTool(Tool):
    """Load a skill's instructions by name."""

    def __init__(self, manager: SkillManager, *, name: str = "skill") -> None:
        self._manager = manager
        super().__init__(
            name=name,
            description=_SKILL_DESC,
            params=_SKILL_PARAMS,
            func=self._execute,
            tags=frozenset({"skills"}),
            summary_key="name",
        )

    def _execute(self, args: dict) -> str:
        skill_name = args["name"]
        skill = self._manager.get(skill_name)
        if not skill:
            available = self._manager.names()
            hint = f" Available: {available}" if available else " No skills available."
            raise ToolError(f"Skill '{skill_name}' not found.{hint}", "not_found")
        return f"Base directory: {skill.base_dir}\n\n{skill.load_content()}"


def _one_line(text: str, limit: int = 100) -> str:
    """Collapse a skill description to a single readable line for /help."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return first[: limit - 1] + "…" if len(first) > limit else first


def make_skill_command(skill: Skill) -> Command:
    """A ``/skill:<name>`` command that injects the skill's instructions."""
    skill_name = skill.metadata.name

    async def _handle(ctx: CommandContext) -> CommandResult:
        content = skill.load_content()
        if not content:
            await ctx.conversation.notify(
                f"Skill '{skill_name}' has no content.", level="warn"
            )
            return CONTINUE
        prompt = (
            f"[Skill:{skill_name} — instructions below, do NOT call the skill tool]"
            f"\n\n{content}"
        )
        if ctx.args:
            prompt += f"\n\n---\n\nUser request: {ctx.args}"
        return CommandResult.text(prompt)

    return Command(
        name=f"/skill:{skill_name}",
        description=_one_line(skill.metadata.description),
        handler=_handle,
    )


# ── Plugin ──────────────────────────────────────────────────


def _render_skills(manager: SkillManager):
    def render(_ctx: dict) -> list[Section]:
        return [
            Section(
                "skill",
                skill.metadata.description,
                attrs={"name": skill.metadata.name, "path": skill.base_dir},
            )
            for skill in manager.all()
        ]

    return render


class SkillsPlugin(Plugin):
    name = "skills"
    description = "Reusable instructions discovered from skill directories"

    def build(self, ctx: HostContext) -> None:
        manager = SkillManager(
            [
                # Portable skills a plugin ships. First, so that a user's own
                # copy of a skill shadows the one a plugin brought.
                *[source / "skills" for source in ctx.plugin_sources],
                ctx.home / "skills",
                ctx.cwd / ".mocode" / "skills",
            ]
        )
        ctx.tools.register(SkillTool(manager))
        ctx.prompt_sections.append(
            Section("skills", _render_skills(manager), priority=50)
        )
        for skill in manager.all():
            ctx.register(make_skill_command(skill))


PLUGIN = SkillsPlugin()
