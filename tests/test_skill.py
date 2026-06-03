"""Tests for core/skill.py, tools/skill.py, and mocode/skills/."""

from pathlib import Path

import pytest

from mocode.core import ToolError
from mocode.core.skill import Skill, SkillManager, SkillMetadata
from mocode.tools.skill import SkillTool
from mocode.skills import WorkflowSkill


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
# Skill.builtin
# ---------------------------------------------------------------------------


class TestSkillBuiltin:
    def test_creates_skill_with_embedded_content(self):
        skill = Skill.builtin("test-skill", "A test", "Hello from builtin")
        assert skill.metadata.name == "test-skill"
        assert skill.metadata.description == "A test"
        assert skill.load_content() == "Hello from builtin"
        assert skill._builtin is True
        assert "<builtin:" in str(skill.path)

    def test_load_content_returns_embedded_content(self):
        skill = Skill.builtin("x", "desc", "content")
        assert skill.load_content() == "content"

    def test_builtin_flag_is_true(self):
        skill = Skill.builtin("x", "desc", "body")
        assert skill._builtin is True

    def test_directory_skill_has_builtin_false(self, tmp_path: Path):
        skill_dir = tmp_path / "normal"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: normal\ndescription: normal\n---\n\nBody\n", encoding="utf-8"
        )
        skill = Skill(path=skill_dir, metadata=SkillMetadata(name="normal", description="normal"))
        assert skill._builtin is False


# ---------------------------------------------------------------------------
# SkillManager — built-in registration
# ---------------------------------------------------------------------------


class TestSkillManagerBuiltin:
    def test_register_adds_skill(self):
        mgr = SkillManager()
        skill = Skill.builtin("my-skill", "desc", "content")
        mgr.register(skill)
        assert mgr.names() == ["my-skill"]
        assert mgr.get("my-skill") is skill

    def test_registered_skill_appears_in_all_metadata(self):
        mgr = SkillManager()
        mgr.register(Skill.builtin("a", "desc a", "body a"))
        mgr.register(Skill.builtin("b", "desc b", "body b"))
        names = {m.name for m in mgr.all_metadata()}
        assert names == {"a", "b"}

    def test_builtin_takes_priority_over_discovered(self, tmp_path: Path):
        """Built-in skill with same name as discovered skill wins."""
        _make_skill_dir(tmp_path, "shared", "discovered desc", "discovered body")
        mgr = SkillManager([tmp_path])
        mgr.register(Skill.builtin("shared", "builtin desc", "builtin body"))
        skill = mgr.get("shared")
        assert skill is not None
        assert skill.load_content() == "builtin body"
        assert skill._builtin is True

    def test_discover_does_not_clear_builtins(self, tmp_path: Path):
        mgr = SkillManager()
        mgr.register(Skill.builtin("keep-me", "desc", "content"))
        # discover with empty dir
        mgr._skill_dirs = [tmp_path]
        mgr.discover()
        assert "keep-me" in mgr.names()
        assert mgr.get("keep-me").load_content() == "content"

    def test_names_includes_both(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "discovered", "desc", "body")
        mgr = SkillManager([tmp_path])
        mgr.register(Skill.builtin("builtin", "desc", "body"))
        names = mgr.names()
        assert "builtin" in names
        assert "discovered" in names

    def test_nested_builtin_then_discover(self, tmp_path: Path):
        """Built-in survives discover() call."""
        mgr = SkillManager()
        mgr.register(Skill.builtin("survivor", "desc", "content"))
        _make_skill_dir(tmp_path, "new-guy", "desc", "body")
        mgr._skill_dirs = [tmp_path]
        mgr.discover()
        assert "survivor" in mgr.names()
        assert "new-guy" in mgr.names()


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

    def test_builtin_skill_returns_content_without_directory(self):
        """Built-in skills should not show 'Base directory' in output."""
        mgr = SkillManager()
        mgr.register(Skill.builtin("builtin-skill", "desc", "builtin content"))
        tool = SkillTool(mgr)
        result = tool.run({"name": "builtin-skill"})
        assert "Base directory:" not in result
        assert result == "builtin content"

    def test_builtin_skill_works_with_discovered(self, tmp_path: Path):
        """Built-in and discovered skills coexist in SkillTool."""
        _make_skill_dir(tmp_path, "discovered", "desc", "discovered body")
        mgr = SkillManager([tmp_path])
        mgr.register(Skill.builtin("builtin", "desc", "builtin body"))
        tool = SkillTool(mgr)

        disc_result = tool.run({"name": "discovered"})
        assert "Base directory:" in disc_result
        assert "discovered body" in disc_result

        builtin_result = tool.run({"name": "builtin"})
        assert "Base directory:" not in builtin_result
        assert builtin_result == "builtin body"

# ---------------------------------------------------------------------------
# WorkflowSkill (built-in skill factory)
# ---------------------------------------------------------------------------


class TestWorkflowSkill:
    def test_returns_builtin_skill(self):
        skill = WorkflowSkill()
        assert skill._builtin is True
        assert skill.metadata.name == "workflow"
        assert "MoCode Workflows" in skill.metadata.description

    def test_content_describes_workflows(self):
        skill = WorkflowSkill()
        content = skill.load_content()
        assert "MoCode Workflows" in content
        assert "/workflow" in content
        assert "DAG" in content or "nodes" in content

    def test_registers_and_resolves_via_manager(self):
        mgr = SkillManager()
        mgr.register(WorkflowSkill())
        skill = mgr.get("workflow")
        assert skill is not None
        assert "MoCode Workflows" in skill.load_content()

    def test_resolves_via_skill_tool(self):
        mgr = SkillManager()
        mgr.register(WorkflowSkill())
        tool = SkillTool(mgr)
        result = tool.run({"name": "workflow"})
        assert "Base directory:" not in result
        assert "MoCode Workflows" in result
