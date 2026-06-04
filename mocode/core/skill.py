"""Skill discovery and loading — directory-based and built-in skills.

Usage:
    from mocode.core.skill import SkillManager

    mgr = SkillManager([Path.home() / ".mocode" / "skills"])
    skill = mgr.get("fastapi")
    content = skill.load_content()

Built-in skills are registered programmatically (no filesystem):
    skill = Skill.builtin(name="my-skill", description="...", content="...")
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
    path: Path
    metadata: SkillMetadata
    _content: str | None = None
    _builtin: bool = False
    virtual_files: dict[str, str] = field(default_factory=dict)

    @classmethod
    def builtin(
        cls,
        name: str,
        description: str,
        content: str,
        *,
        virtual_files: dict[str, str] | None = None,
    ) -> Skill:
        """Create a built-in skill with embedded content (no filesystem)."""
        return cls(
            path=Path(f"<builtin:{name}>"),
            metadata=SkillMetadata(name=name, description=description),
            _content=content,
            _builtin=True,
            virtual_files=virtual_files or {},
        )

    @property
    def skill_md_path(self) -> Path:
        return self.path / "SKILL.md"

    def load_content(self) -> str:
        if self._content is None:
            self._content = self._read_body()
        return self._content

    def _read_body(self) -> str:
        return read_skill(self.path)[1]


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


def discover_references(skill_dir: Path, skill_name: str) -> dict[str, str]:
    """Auto-discover ALL reference files in a skill directory.

    Skips: SKILL.md, __init__.py, __pycache__/ dirs, .pyc files.
    Returns dict mapping vfs://{skill_name}/{relative_path} → file content.
    Text files are read as UTF-8; binary files are skipped gracefully.
    """
    result: dict[str, str] = {}
    if not skill_dir.is_dir():
        return result
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.name in ("SKILL.md", "__init__.py"):
            continue
        if "__pycache__" in f.parts:
            continue
        if f.suffix == ".pyc":
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = f.relative_to(skill_dir).as_posix()
        result[f"vfs://{skill_name}/{rel}"] = content
    return result


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
        """Register a built-in skill. Skipped if a discovered skill with the same name exists."""
        if skill.metadata.name not in self._skills:
            self._builtin_skills[skill.metadata.name] = skill
            self._mount_to_vfs(skill)

    def discover(self) -> None:
        """Re-discover directory-based skills. Built-in skills are NOT cleared."""
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
        skill_md = path / "SKILL.md"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        fm = _parse_frontmatter(text)
        if not fm:
            return None
        meta = SkillMetadata.from_dict(fm)
        if not meta.name:
            return None
        skill = Skill(path=path, metadata=meta)
        skill.virtual_files = discover_references(path, meta.name)
        self._mount_to_vfs(skill)
        return skill

    def _mount_to_vfs(self, skill: Skill) -> None:
        if self._vfs is None:
            return
        for path, content in skill.virtual_files.items():
            self._vfs.add(path, content)

    def get(self, name: str) -> Skill | None:
        """Look up a skill by name."""
        if name in self._skills:
            return self._skills[name]
        return self._builtin_skills.get(name)

    def all_metadata(self) -> list[SkillMetadata]:
        """Return metadata for all skills (discovered first, then built-in)."""
        result = [s.metadata for s in self._skills.values()]
        result.extend(s.metadata for s in self._builtin_skills.values())
        return result

    def names(self) -> list[str]:
        """Return all skill names (discovered first, then built-in)."""
        return list(self._skills.keys()) + list(self._builtin_skills.keys())
