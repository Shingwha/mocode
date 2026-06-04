"""Tests for core/skill.py, tools/skill.py, and mocode/skills/."""

from pathlib import Path

import pytest

from mocode.core import ToolError
from mocode.core.skill import Skill, SkillManager, SkillMetadata, make_builtin_skill
from mocode.core.virtualfs import VirtualFS
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

    def test_from_dict_extra_fields_go_to_attrs(self):
        meta = SkillMetadata.from_dict(
            {
                "name": "react",
                "description": "React patterns",
                "license": "MIT",
                "compatibility": "0.3",
                "author": "alice",
            }
        )
        assert meta.attrs == {
            "license": "MIT",
            "compatibility": "0.3",
            "author": "alice",
        }

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

    def test_create_with_vfs_uri(self):
        skill = Skill(
            metadata=SkillMetadata(name="my-skill", description="desc"),
            vfs_uri="vfs://my-skill/",
            _content="body",
        )
        assert skill.vfs_uri == "vfs://my-skill/"
        assert skill.path is None
        assert skill.load_content() == "body"

    def test_base_dir_with_vfs_uri(self):
        skill = Skill(
            metadata=SkillMetadata(name="x", description=""),
            vfs_uri="vfs://x/",
        )
        assert skill.base_dir == "vfs://x/"

    def test_base_dir_with_path(self, tmp_path: Path):
        skill = Skill(
            path=tmp_path,
            metadata=SkillMetadata(name="x", description=""),
        )
        assert skill.base_dir == str(tmp_path)

    def test_base_dir_empty(self):
        skill = Skill(metadata=SkillMetadata(name="x", description=""))
        assert skill.base_dir == ""


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

    def test_default_name_fallback(self, tmp_path: Path):
        skill_dir = tmp_path / "anon"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("Just body", encoding="utf-8")
        skill = make_builtin_skill(skill_dir, default_name="anon")
        assert skill.metadata.name == "anon"


# ---------------------------------------------------------------------------
# SkillManager — built-in registration
# ---------------------------------------------------------------------------


class TestSkillManagerRegistered:
    def test_register_adds_skill(self):
        mgr = SkillManager()
        skill = _make_registered_skill("my-skill", "desc", "content")
        mgr.register(skill)
        assert mgr.names() == ["my-skill"]
        assert mgr.get("my-skill") is skill

    def test_registered_skill_appears_in_all_metadata(self):
        mgr = SkillManager()
        mgr.register(_make_registered_skill("a", "desc a", "body a"))
        mgr.register(_make_registered_skill("b", "desc b", "body b"))
        names = {m.name for m in mgr.all_metadata()}
        assert names == {"a", "b"}

    def test_discovered_overrides_registered(self, tmp_path: Path):
        """Discovered skill overrides registered skill with the same name."""
        _make_skill_dir(tmp_path, "shared", "discovered desc", "discovered body")
        mgr = SkillManager([tmp_path])
        mgr.register(_make_registered_skill("shared", "registered desc", "registered body"))
        skill = mgr.get("shared")
        assert skill is not None
        assert skill.load_content() == "discovered body"

    def test_discover_does_not_clear_registered(self, tmp_path: Path):
        mgr = SkillManager()
        mgr.register(_make_registered_skill("keep-me", "desc", "content"))
        # discover with empty dir
        mgr._skill_dirs = [tmp_path]
        mgr.discover()
        assert "keep-me" in mgr.names()
        assert mgr.get("keep-me").load_content() == "content"

    def test_names_includes_both(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "discovered", "desc", "body")
        mgr = SkillManager([tmp_path])
        mgr.register(_make_registered_skill("registered", "desc", "body"))
        names = mgr.names()
        assert "registered" in names
        assert "discovered" in names

    def test_registered_then_discover(self, tmp_path: Path):
        """Registered skill survives discover() call."""
        mgr = SkillManager()
        mgr.register(_make_registered_skill("survivor", "desc", "content"))
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
        _make_skill_dir(
            tmp_path, "fastapi", "FastAPI tips", "Use dependency injection."
        )
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
        (d / "SKILL.md").write_text(
            "---\ndescription: no name\n---\nbody", encoding="utf-8"
        )
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
# SkillManager VFS integration
# ---------------------------------------------------------------------------


class TestSkillManagerVFS:
    def test_discover_mounts_to_vfs(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "my-skill", "desc")
        (skill_dir / "ref.md").write_text("ref content", encoding="utf-8")

        vfs = VirtualFS()
        mgr = SkillManager([tmp_path], vfs=vfs)
        assert vfs.get("vfs://my-skill/ref.md") == "ref content"

    def test_register_mounts_real_path_to_vfs(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "builtin", "desc", "body")
        (skill_dir / "data.md").write_text("data content", encoding="utf-8")

        vfs = VirtualFS()
        mgr = SkillManager(vfs=vfs)
        skill = Skill(
            path=skill_dir,
            metadata=SkillMetadata(name="builtin", description="desc"),
            _content="body",
        )
        mgr.register(skill)
        assert vfs.get("vfs://builtin/data.md") == "data content"


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

    def test_registered_skill_returns_content_with_vfs_uri(self):
        """Registered skills show vfs_uri as base directory."""
        mgr = SkillManager()
        mgr.register(_make_registered_skill("reg-skill", "desc", "reg content"))
        tool = SkillTool(mgr)
        result = tool.run({"name": "reg-skill"})
        assert "Base directory: vfs://reg-skill/" in result
        assert "reg content" in result

    def test_all_skills_show_base_directory(self, tmp_path: Path):
        """Both registered and discovered skills show 'Base directory'."""
        _make_skill_dir(tmp_path, "discovered", "desc", "discovered body")
        mgr = SkillManager([tmp_path])
        mgr.register(_make_registered_skill("registered", "desc", "registered body"))
        tool = SkillTool(mgr)

        disc_result = tool.run({"name": "discovered"})
        assert "Base directory:" in disc_result
        assert "discovered body" in disc_result

        reg_result = tool.run({"name": "registered"})
        assert "Base directory: vfs://registered/" in reg_result
        assert "registered body" in reg_result


# ---------------------------------------------------------------------------
# WorkflowSkill (built-in skill factory — reads from package data)
# ---------------------------------------------------------------------------


class TestWorkflowSkill:
    def test_returns_skill_with_vfs_uri(self):
        skill = WorkflowSkill()
        assert skill.vfs_uri == "vfs://workflow/"
        assert skill.metadata.name == "workflow"
        assert "MoCode Workflows" in skill.metadata.description

    def test_has_real_path(self):
        skill = WorkflowSkill()
        assert skill.path is not None
        assert skill.path.is_dir()

    def test_content_describes_workflows(self):
        skill = WorkflowSkill()
        content = skill.load_content()
        assert "MoCode Workflows" in content
        assert "vfs://workflow/" in content
        assert "DAG" in content or "nodes" in content

    def test_registers_and_resolves_via_manager(self):
        vfs = VirtualFS()
        mgr = SkillManager(vfs=vfs)
        mgr.register(WorkflowSkill())
        skill = mgr.get("workflow")
        assert skill is not None
        assert "MoCode Workflows" in skill.load_content()
        # Reference files should be mounted
        assert vfs.glob("vfs://workflow/*.md")

    def test_resolves_via_skill_tool(self):
        vfs = VirtualFS()
        mgr = SkillManager(vfs=vfs)
        mgr.register(WorkflowSkill())
        tool = SkillTool(mgr)
        result = tool.run({"name": "workflow"})
        assert "Base directory: vfs://workflow/" in result
        assert "MoCode Workflows" in result
