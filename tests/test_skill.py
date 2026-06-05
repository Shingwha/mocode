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
    _read_skill_md,
)
from mocode.core.virtualfs import VirtualFS
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

    def test_incomplete_frontmatter(self):
        text = "---\nname: test"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n---\nBody"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == "Body"


class TestReadSkillMd:
    def test_reads_and_parses(self, tmp_path: Path):
        (tmp_path / "SKILL.md").write_text(
            "---\nname: test\ndescription: desc\n---\n\nHello",
            encoding="utf-8",
        )
        fm, body = _read_skill_md(tmp_path)
        assert fm["name"] == "test"
        assert body == "Hello"

    def test_missing_file(self, tmp_path: Path):
        fm, body = _read_skill_md(tmp_path)
        assert fm == {}
        assert body == ""


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


class TestSkillManagerRegistered:
    def test_register_adds_skill(self):
        mgr = SkillManager()
        skill = _make_registered_skill("my-skill", "desc", "content")
        mgr.register(skill)
        assert mgr.names() == ["my-skill"]
        assert mgr.get("my-skill") is skill

    def test_discovered_overrides_registered(self, tmp_path: Path):
        """Discovered skill overrides registered skill with the same name."""
        _make_skill_dir(tmp_path, "shared", "discovered desc", "discovered body")
        mgr = SkillManager([tmp_path])
        mgr.register(_make_registered_skill("shared", "registered desc", "registered body"))
        skill = mgr.get("shared")
        assert skill is not None
        assert skill.load_content() == "discovered body"


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

    def test_get(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use DI.")
        mgr = SkillManager([tmp_path])
        skill = mgr.get("fastapi")
        assert skill is not None
        assert skill.load_content() == "Use DI."

    def test_multiple_dirs(self, tmp_path: Path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _make_skill_dir(dir_a, "skill-a", "from dir a")
        _make_skill_dir(dir_b, "skill-b", "from dir b")
        mgr = SkillManager([dir_a, dir_b])
        assert set(mgr.names()) == {"skill-a", "skill-b"}

    def test_discover_does_not_mount_to_vfs(self, tmp_path: Path):
        """Discovered (external) skills are NOT mounted into VFS."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: test\n---\n\nBody",
            encoding="utf-8",
        )
        (skill_dir / "ref.md").write_text("reference content", encoding="utf-8")
        vfs = VirtualFS()
        mgr = SkillManager([tmp_path], vfs=vfs)
        # Only built-in (registered) skills are mounted; discovered ones are not
        assert not vfs.exists("vfs://my-skill/ref.md")
        assert not vfs.exists("vfs://my-skill/SKILL.md")
        # But the skill is still discoverable
        assert mgr.get("my-skill") is not None

    def test_register_mounts_to_vfs(self):
        """Registered skill with path gets mounted into VFS."""
        vfs = VirtualFS()
        mgr = SkillManager(vfs=vfs)
        skill = Skill(
            metadata=SkillMetadata(name="test", description="desc"),
            path=Path("/nonexistent"),  # _mount_to_vfs checks is_dir
            vfs_uri="vfs://test/",
        )
        mgr.register(skill)
        # path doesn't exist, so nothing mounted — but registration works
        assert mgr.get("test") is skill


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
