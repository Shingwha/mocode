"""Tests for core/skill.py, tools/skill.py, and mocode/skills/."""

from pathlib import Path

import pytest

from mocode.core import ToolError
from mocode.core.skill import (
    Skill,
    SkillManager,
    SkillMetadata,
    make_builtin_skill,
    _parse_frontmatter,
)
from mocode.tools.skill import SkillTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_dir(base: Path, name: str, description: str, body: str = "") -> Path:
    """Create a minimal skill directory under *base*."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n"
    (skill_dir / "SKILL.md").write_text(frontmatter + body, encoding="utf-8")
    return skill_dir


def _make_registered_skill(name: str, description: str, content: str) -> Skill:
    """Create a Skill with vfs_uri for registration tests."""
    return Skill(
        metadata=SkillMetadata(name=name, description=description),
        vfs_uri=f"vfs://{name}/",
        _content=content,
    )


# ---------------------------------------------------------------------------
# SkillMetadata
# ---------------------------------------------------------------------------


class TestSkillMetadata:
    def test_from_dict_basic(self):
        meta = SkillMetadata.from_dict(
            {"name": "fastapi", "description": "FastAPI tips"}
        )
        assert meta.name == "fastapi"
        assert meta.description == "FastAPI tips"
        assert meta.attrs == {}


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\nname: test\ndescription: desc\n---\n\nBody here"
        fm, body = _parse_frontmatter(text)
        assert fm["name"] == "test"
        assert fm["description"] == "desc"
        assert body == "Body here"

    def test_no_frontmatter(self):
        text = "Just plain text"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == "Just plain text"


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


class TestSkill:
    def test_load_content_strips_frontmatter(self, tmp_path: Path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: test\n---\n\nHello world\n",
            encoding="utf-8",
        )
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="my-skill", description="test"))
        assert skill.load_content() == "Hello world"

    def test_load_content_missing_file(self, tmp_path: Path):
        skill_dir = tmp_path / "missing"
        skill_dir.mkdir()
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="missing", description=""))
        assert skill.load_content() == ""


# ---------------------------------------------------------------------------
# make_builtin_skill
# ---------------------------------------------------------------------------


class TestMakeBuiltinSkill:
    def test_creates_skill_with_both_path_and_vfs_uri(self, tmp_path: Path):
        skill_dir = tmp_path / "my-builtin"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-builtin\ndescription: A test skill\n---\n\nBody here",
            encoding="utf-8",
        )
        skill = make_builtin_skill(skill_dir)
        assert skill.metadata.name == "my-builtin"
        assert skill.metadata.description == "A test skill"
        assert skill.path == skill_dir
        assert skill.vfs_uri == "vfs://my-builtin/"
        assert skill.load_content() == "Body here"


# ---------------------------------------------------------------------------
# SkillManager — built-in registration
# ---------------------------------------------------------------------------


class TestSkillManager:
    def test_register_adds_skill(self):
        mgr = SkillManager()
        skill = _make_registered_skill("my-skill", "desc", "content")
        mgr.register(skill)
        assert mgr.names() == ["my-skill"]
        assert mgr.get("my-skill") is skill

    def test_discover_finds_skills(self, tmp_path: Path):
        _make_skill_dir(
            tmp_path, "fastapi", "FastAPI tips", "Use dependency injection."
        )
        _make_skill_dir(tmp_path, "react", "React patterns", "Use hooks.")
        mgr = SkillManager([tmp_path])
        assert mgr.names() == ["fastapi", "react"]

    def test_get(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use DI.")
        mgr = SkillManager([tmp_path])
        skill = mgr.get("fastapi")
        assert skill is not None
        assert skill.load_content() == "Use DI."


# ---------------------------------------------------------------------------
# SkillTool
# ---------------------------------------------------------------------------


class TestSkillTool:
    def test_returns_content(self, tmp_path: Path):
        _make_skill_dir(
            tmp_path, "fastapi", "FastAPI tips", "Use dependency injection."
        )
        mgr = SkillManager([tmp_path])
        tool = SkillTool(mgr)
        result = tool.run({"name": "fastapi"})
        assert "Base directory:" in result
        assert "Use dependency injection." in result

    def test_not_found_raises(self, tmp_path: Path):
        mgr = SkillManager([tmp_path])
        tool = SkillTool(mgr)
        with pytest.raises(ToolError) as exc_info:
            tool.run({"name": "nope"})
        assert exc_info.value.code == "not_found"
