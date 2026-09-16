"""Tests for the skills plugin — Skill, SkillManager, SkillTool."""

from pathlib import Path

import pytest

from mocode.app.plugin.builtin.skills import (
    Skill,
    SkillManager,
    SkillMetadata,
    SkillTool,
)
from mocode.app.plugin.loader import parse_frontmatter
from mocode.core import ToolError


def _make_skill_dir(base: Path, name: str, description: str, body: str = "") -> Path:
    """Create a minimal skill directory under *base*."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n"
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return skill_dir


class TestSkillMetadata:
    def test_from_dict_basic(self):
        meta = SkillMetadata.from_dict({"name": "fastapi", "description": "FastAPI tips"})
        assert meta.name == "fastapi"
        assert meta.description == "FastAPI tips"
        assert meta.attrs == {}

    def test_from_dict_keeps_extra_keys_as_attrs(self):
        meta = SkillMetadata.from_dict({"name": "x", "description": "d", "version": "1"})
        assert meta.attrs == {"version": "1"}


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        fm, body = parse_frontmatter("---\nname: test\ndescription: desc\n---\n\nBody here")
        assert fm["name"] == "test"
        assert fm["description"] == "desc"
        assert body == "Body here"

    def test_no_frontmatter(self):
        fm, body = parse_frontmatter("Just plain text")
        assert fm == {}
        assert body == "Just plain text"


class TestSkill:
    def test_load_content_strips_frontmatter(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "my-skill", "test", "Hello world\n")
        skill = Skill.from_dir(skill_dir)
        assert skill is not None
        assert skill.load_content() == "Hello world"
        assert skill.base_dir == str(skill_dir)

    def test_from_dir_without_name_returns_none(self, tmp_path: Path):
        skill_dir = tmp_path / "nameless"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: no name\n---\n", encoding="utf-8")
        assert Skill.from_dir(skill_dir) is None

    def test_load_content_missing_file(self, tmp_path: Path):
        skill_dir = tmp_path / "missing"
        skill_dir.mkdir()
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="missing", description=""))
        assert skill.load_content() == ""


class TestSkillManager:
    def test_register_adds_skill(self):
        mgr = SkillManager()
        skill = Skill(metadata=SkillMetadata(name="mine", description="d"), _content="body")
        mgr.register(skill)
        assert mgr.names() == ["mine"]
        assert mgr.get("mine") is skill

    def test_discover_finds_skills(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")
        _make_skill_dir(tmp_path, "react", "React patterns", "Use hooks.")
        mgr = SkillManager([tmp_path])
        assert mgr.names() == ["fastapi", "react"]

    def test_discovered_skill_wins_over_registered(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "On disk")
        mgr = SkillManager([tmp_path])
        mgr.register(
            Skill(metadata=SkillMetadata(name="fastapi", description="in code"), _content="x")
        )
        assert mgr.get("fastapi").metadata.description == "On disk"

    def test_missing_directory_is_ignored(self, tmp_path: Path):
        mgr = SkillManager([tmp_path / "nope"])
        assert mgr.all() == []


class TestSkillTool:
    def test_returns_content_and_base_dir(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")
        tool = SkillTool(SkillManager([tmp_path]))
        result = tool.run({"name": "fastapi"})
        assert "Base directory:" in result
        assert str(skill_dir) in result
        assert "Use dependency injection." in result

    def test_not_found_raises(self, tmp_path: Path):
        tool = SkillTool(SkillManager([tmp_path]))
        with pytest.raises(ToolError) as exc_info:
            tool.run({"name": "nope"})
        assert exc_info.value.code == "not_found"
