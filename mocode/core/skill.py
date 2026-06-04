"""Skill discovery and loading — directory-based skills.

Usage:
    from mocode.core.skill import SkillManager

    mgr = SkillManager([Path.home() / ".mocode" / "skills"], vfs=vfs)
    skill = mgr.get("fastapi")
    content = skill.load_content()

Skills can also be registered programmatically:
    skill = Skill(vfs_uri="vfs://my-skill/", metadata=..., _content="...")
    mgr.register(skill)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mocode.core.virtualfs import VirtualFS


@dataclass
class SkillMetadata:
    name: str
    description: str
    attrs: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SkillMetadata:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            attrs={k: v for k, v in data.items() if k not in ("name", "description")},
        )


@dataclass
class Skill:
    """A skill with instructions and optional reference files.

    Exactly one of *path* (real directory) or *vfs_uri* (virtual) is set.
    """

    metadata: SkillMetadata
    path: Path | None = None
    vfs_uri: str | None = None
    _content: str | None = None

    @property
    def base_dir(self) -> str:
        """Human-readable base directory for display."""
        if self.vfs_uri:
            return self.vfs_uri
        return str(self.path) if self.path else ""

    def load_content(self) -> str:
        if self._content is None:
            self._content = self._read_body()
        return self._content

    def _read_body(self) -> str:
        if self.path is None:
            return ""
        return _read_skill_body(self.path)


def _parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        import yaml

        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return None


def _read_skill_body(skill_dir: Path) -> str:
    """Read SKILL.md body (after frontmatter) from *skill_dir*."""
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    fm = _parse_frontmatter(text)
    if fm is not None:
        parts = text.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else text
    return text


def read_skill(skill_dir: Path) -> tuple[dict, str]:
    """Read SKILL.md from *skill_dir*.

    Returns ``(frontmatter_dict, body_content)``.
    Frontmatter parsing failure → ``({}, raw_text)``.
    File missing / unreadable → ``({}, "")``.
    """
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    fm = _parse_frontmatter(text)
    if fm is not None:
        parts = text.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else text
        return fm, body
    return {}, text


def make_builtin_skill(pkg_dir: Path, *, default_name: str = "") -> Skill:
    """Create a Skill from a package directory with SKILL.md.

    Reads frontmatter for name/description, pre-loads body content.
    *pkg_dir* is kept as ``path`` so ``SkillManager`` can mount references.
    """
    fm, content = read_skill(pkg_dir)
    name = fm.get("name", default_name)
    description = fm.get("description", "")
    return Skill(
        metadata=SkillMetadata(name=name, description=description),
        path=pkg_dir,
        vfs_uri=f"vfs://{name}/",
        _content=content,
    )


class SkillManager:
    def __init__(
        self, skill_dirs: list[Path] | None = None, *, vfs: VirtualFS | None = None
    ):
        self._skill_dirs: list[Path] = list(skill_dirs) if skill_dirs else []
        self._vfs = vfs
        self._skills: dict[str, Skill] = {}  # directory-discovered
        self._builtin_skills: dict[str, Skill] = {}  # programmatically registered
        self.discover()

    def register(self, skill: Skill) -> None:
        """Register a skill programmatically.  Skipped if a discovered skill with the same name exists."""
        if skill.metadata.name not in self._skills:
            self._builtin_skills[skill.metadata.name] = skill
            self._mount_vfs(skill)

    def discover(self) -> None:
        """Re-discover directory-based skills.  Registered skills are NOT cleared."""
        self._skills.clear()
        for d in self._skill_dirs:
            if not d.is_dir():
                continue
            for child in sorted(d.iterdir()):
                if child.is_dir() and (child / "SKILL.md").is_file():
                    skill = self._load_skill(child)
                    if skill:
                        self._skills[skill.metadata.name] = skill

    def _load_skill(self, path: Path) -> Skill | None:
        fm, _body = read_skill(path)
        if not fm:
            return None
        meta = SkillMetadata.from_dict(fm)
        if not meta.name:
            return None
        skill = Skill(path=path, metadata=meta)
        self._mount_vfs(skill)
        return skill

    def _mount_vfs(self, skill: Skill) -> None:
        """Mount skill reference files into VFS."""
        if self._vfs is None:
            return
        if skill.path is not None:
            self._vfs.mount_directory(skill.path, skill.metadata.name)

    def get(self, name: str) -> Skill | None:
        """Look up a skill by name."""
        if name in self._skills:
            return self._skills[name]
        return self._builtin_skills.get(name)

    def all(self) -> list[Skill]:
        """Return all skills (discovered first, then registered)."""
        result = list(self._skills.values())
        result.extend(self._builtin_skills.values())
        return result

    def all_metadata(self) -> list[SkillMetadata]:
        """Return metadata for all skills (discovered first, then registered)."""
        return [s.metadata for s in self.all()]

    def names(self) -> list[str]:
        """Return all skill names (discovered first, then registered)."""
        return list(self._skills.keys()) + list(self._builtin_skills.keys())
