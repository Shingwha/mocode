"""Tests for core/skill.py and tools/skill.py."""

from pathlib import Path

import pytest

from mocode.core import ToolError
from mocode.core.skill import Skill, SkillManager, SkillMetadata
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


# ---------------------------------------------------------------------------
# SkillMetadata
# ---------------------------------------------------------------------------


class TestSkillMetadata:
    def test_from_dict_basic(self):
        meta = SkillMetadata.from_dict({"name": "fastapi", "description": "FastAPI tips"})
        assert meta.name == "fastapi"
        assert meta.description == "FastAPI tips"
        assert meta.attrs == {}

    def test_from_dict_extra_fields_go_to_attrs(self):
        meta = SkillMetadata.from_dict({
            "name": "react",
            "description": "React patterns",
            "license": "MIT",
            "compatibility": "0.3",
            "author": "alice",
        })
        assert meta.attrs == {"license": "MIT", "compatibility": "0.3", "author": "alice"}

    def test_from_dict_missing_fields(self):
        meta = SkillMetadata.from_dict({})
        assert meta.name == ""
        assert meta.description == ""


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

    def test_load_content_caches(self, tmp_path: Path):
        skill_dir = tmp_path / "cached"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: cached\ndescription: d\n---\n\nBody\n", encoding="utf-8"
        )
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="cached", description="d"))
        _ = skill.load_content()
        assert skill._content == "Body"
        # Overwrite file — cached content should not change
        (skill_dir / "SKILL.md").write_text("---\n---\nNew body\n", encoding="utf-8")
        assert skill.load_content() == "Body"

    def test_load_content_no_frontmatter(self, tmp_path: Path):
        skill_dir = tmp_path / "raw"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Just plain text", encoding="utf-8")
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="raw", description=""))
        assert skill.load_content() == "Just plain text"

    def test_load_content_missing_file(self, tmp_path: Path):
        skill_dir = tmp_path / "missing"
        skill_dir.mkdir()
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="missing", description=""))
        assert skill.load_content() == ""


# ---------------------------------------------------------------------------
# SkillManager
# ---------------------------------------------------------------------------


class TestSkillManager:
    def test_discover_finds_skills(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")
        _make_skill_dir(tmp_path, "react", "React patterns", "Use hooks.")
        mgr = SkillManager([tmp_path])
        assert mgr.names() == ["fastapi", "react"]

    def test_discover_empty_dir(self, tmp_path: Path):
        mgr = SkillManager([tmp_path])
        assert mgr.names() == []

    def test_discover_skips_non_skill_dirs(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "valid", "A valid skill")
        (tmp_path / "no_skill_md").mkdir()
        assert SkillManager([tmp_path]).names() == ["valid"]

    def test_discover_skips_no_name(self, tmp_path: Path):
        d = tmp_path / "noname"
        d.mkdir()
        (d / "SKILL.md").write_text("---\ndescription: no name\n---\nbody", encoding="utf-8")
        assert SkillManager([tmp_path]).names() == []

    def test_discover_no_dirs(self):
        mgr = SkillManager()
        assert mgr.names() == []

    def test_discover_nonexistent_dir(self, tmp_path: Path):
        mgr = SkillManager([tmp_path / "does_not_exist"])
        assert mgr.names() == []

    def test_get(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use DI.")
        mgr = SkillManager([tmp_path])
        skill = mgr.get("fastapi")
        assert skill is not None
        assert skill.load_content() == "Use DI."

    def test_get_missing(self, tmp_path: Path):
        mgr = SkillManager([tmp_path])
        assert mgr.get("nope") is None

    def test_all_metadata(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "a", "desc a")
        _make_skill_dir(tmp_path, "b", "desc b")
        metas = SkillManager([tmp_path]).all_metadata()
        assert len(metas) == 2
        names = {m.name for m in metas}
        assert names == {"a", "b"}

    def test_discover_replaces_index(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "old", "old skill")
        mgr = SkillManager([tmp_path])
        assert mgr.names() == ["old"]
        # Add a new skill dir and re-discover
        _make_skill_dir(tmp_path, "new", "new skill")
        mgr.discover()
        assert set(mgr.names()) == {"old", "new"}

    def test_multiple_dirs(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _make_skill_dir(dir_a, "skill-a", "from dir a")
        _make_skill_dir(dir_b, "skill-b", "from dir b")
        mgr = SkillManager([dir_a, dir_b])
        assert set(mgr.names()) == {"skill-a", "skill-b"}

    def test_later_dir_overwrites_same_name(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _make_skill_dir(dir_a, "shared", "desc", "version A")
        _make_skill_dir(dir_b, "shared", "desc", "version B")
        mgr = SkillManager([dir_a, dir_b])
        skill = mgr.get("shared")
        assert skill is not None
        assert skill.load_content() == "version B"


# ---------------------------------------------------------------------------
# SkillTool
# ---------------------------------------------------------------------------


class TestSkillTool:
    def test_returns_content(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")
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

    def test_missing_name_raises(self, tmp_path: Path):
        mgr = SkillManager([tmp_path])
        tool = SkillTool(mgr)
        with pytest.raises(ToolError) as exc_info:
            tool.run({})
        assert exc_info.value.code == "missing_param"

    def test_custom_name(self, tmp_path: Path):
        mgr = SkillManager([tmp_path])
        tool = SkillTool(mgr, name="load-skill")
        assert tool.name == "load-skill"
