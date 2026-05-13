"""Skill discovery and loading — directory-based, parameterized paths.

Usage:
    from mocode.core.skill import SkillManager

    mgr = SkillManager([Path.home() / ".mocode" / "skills"])
    skill = mgr.get("fastapi")
    content = skill.load_content()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

    @property
    def skill_md_path(self) -> Path:
        return self.path / "SKILL.md"

    def load_content(self) -> str:
        if self._content is None:
            self._content = self._read_body()
        return self._content

    def _read_body(self) -> str:
        try:
            text = self.skill_md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text


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


class SkillManager:
    def __init__(self, skill_dirs: list[Path] | None = None):
        self._skill_dirs: list[Path] = list(skill_dirs) if skill_dirs else []
        self._skills: dict[str, Skill] = {}
        self.discover()

    def discover(self) -> None:
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
        return Skill(path=path, metadata=meta)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all_metadata(self) -> list[SkillMetadata]:
        return [s.metadata for s in self._skills.values()]

    def names(self) -> list[str]:
        return list(self._skills.keys())
