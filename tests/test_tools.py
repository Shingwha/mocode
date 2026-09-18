"""The builtin tools: bash, read, and the skill lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from mocode.host.plugin.builtin.filesystem import ReadTool
from mocode.host.plugin.builtin.shell import BashSession, BashTool
from mocode.host.plugin.builtin.skills import (
    Skill,
    SkillManager,
    SkillMetadata,
    SkillTool,
)
from mocode.host.plugin.loader import parse_frontmatter
from mocode.core import ToolError


class TestBashSession:
    @pytest.mark.asyncio
    async def test_runs_a_command(self):
        result = await BashSession().execute("echo hello")
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_the_exit_code_travels_as_a_detail(self):
        session = BashSession()
        assert (await session.execute("true")).details == {"exit_code": 0}
        assert (await session.execute("exit 3")).details == {"exit_code": 3}

    @pytest.mark.asyncio
    async def test_cd_persists(self, tmp_path: Path):
        session = BashSession()
        result = await session.execute(f"cd {tmp_path}")
        assert str(tmp_path) in result.content
        assert session.cwd == str(tmp_path)

    @pytest.mark.asyncio
    async def test_env_vars_persist_across_commands(self):
        session = BashSession()
        await session.execute("export MY_TEST_VAR=world")
        assert (await session.execute("echo $MY_TEST_VAR")).content == "world"

    @pytest.mark.asyncio
    async def test_restart_clears_state(self):
        session = BashSession()
        await session.execute("export MY_TEST_VAR=hello")
        session.restart()
        assert (await session.execute("echo $MY_TEST_VAR")).content == "(empty)"

    @pytest.mark.asyncio
    async def test_env_values_are_never_executed_as_shell_code(self):
        """Env vars are passed through ``env=``, never interpolated into a script."""
        session = BashSession()
        await session.execute("export EVIL='$(echo INJECTED)'")
        await session.execute("export TICK='`echo INJECTED`'")

        assert (await session.execute("echo $EVIL")).content == "$(echo INJECTED)"
        assert (await session.execute("echo $TICK")).content == "`echo INJECTED`"

    @pytest.mark.asyncio
    async def test_output_is_reported_line_by_line_as_it_arrives(self):
        seen: list[tuple[str, str]] = []

        async def on_output(text: str, stream: str) -> None:
            seen.append((stream, text))

        result = await BashSession().execute(
            "echo one; echo two; echo oops >&2", on_output=on_output
        )

        assert seen == [
            ("stdout", "one\n"),
            ("stdout", "two\n"),
            ("stderr", "oops\n"),
        ]
        assert "one" in result.content and "oops" in result.content

    @pytest.mark.asyncio
    async def test_timeout_kills_the_command(self):
        result = await BashSession().execute("sleep 5", timeout=1)
        assert result.content == "(timed out after 1s)"
        assert "exit_code" not in result.details


class TestBashTool:
    def test_the_tool_asks_for_its_context_so_it_can_stream(self):
        tool = BashTool()
        assert tool.wants_context is True
        assert tool.is_async is True
        assert tool.result_key == "exit_code"

    @pytest.mark.asyncio
    async def test_restart_resets_the_session(self):
        tool = BashTool()
        await tool.run_async({"command": "export V=1"}, None)
        assert (await tool.run_async({"command": "echo $V"}, None)).content == "1"
        assert (await tool.run_async({"command": "x", "restart": True}, None)).content == (
            "Bash session restarted"
        )
        assert (await tool.run_async({"command": "echo $V"}, None)).content == "(empty)"


class TestReadTool:
    def test_a_directory_is_listed_instead_of_erroring(self, tmp_path: Path):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")

        result = ReadTool().run({"path": str(tmp_path)}).content

        assert result.startswith("[")
        assert "subdir/" in result
        assert "hello.py" in result
        assert "1 directories" in result and "1 files" in result

    def test_a_file_reports_how_many_lines_came_back(self, tmp_path: Path):
        path = tmp_path / "a.py"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = ReadTool().run({"path": str(path)})

        assert result.details == {"lines": 3, "total_lines": 3}
        assert "one" in result.content

    def test_a_partial_read_reports_both_numbers(self, tmp_path: Path):
        path = tmp_path / "big.py"
        path.write_text("\n".join(str(i) for i in range(50)), encoding="utf-8")

        result = ReadTool().run({"path": str(path), "offset": 1, "limit": 10})

        assert result.details == {"lines": 10, "total_lines": 50}

    def test_noise_directories_are_skipped(self, tmp_path: Path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "real.py").write_text("x", encoding="utf-8")

        result = ReadTool().run({"path": str(tmp_path)}).content

        assert "__pycache__" not in result
        assert "real.py" in result

    def test_an_empty_directory_is_reported(self, tmp_path: Path):
        result = ReadTool().run({"path": str(tmp_path)}).content
        assert "0 directories" in result and "0 files" in result

    def test_the_result_explains_the_path_is_a_directory(self, tmp_path: Path):
        result = ReadTool().run({"path": str(tmp_path)}).content.lower()
        assert "directory" in result
        assert "bash" in result

    def test_a_directory_reports_no_line_count(self, tmp_path: Path):
        """``result_key`` is lines, and a listing has none — nothing is shown."""
        assert ReadTool().run({"path": str(tmp_path)}).details == {}


def _make_skill_dir(base: Path, name: str, description: str, body: str = "") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return skill_dir


class TestSkills:
    def test_metadata_keeps_unknown_frontmatter_keys(self):
        meta = SkillMetadata.from_dict({"name": "x", "description": "d", "version": "1"})
        assert (meta.name, meta.description, meta.attrs) == ("x", "d", {"version": "1"})

    def test_a_skill_loads_its_body_without_frontmatter(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "my-skill", "test", "Hello world\n")

        skill = Skill.from_dir(skill_dir)

        assert skill.load_content() == "Hello world"
        assert skill.base_dir == str(skill_dir)

    def test_a_skill_without_a_name_is_skipped(self, tmp_path: Path):
        skill_dir = tmp_path / "nameless"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\ndescription: no name\n---\n", encoding="utf-8")
        assert Skill.from_dir(skill_dir) is None

    def test_discovery_prefers_the_directory_over_a_registered_skill(self, tmp_path: Path):
        _make_skill_dir(tmp_path, "fastapi", "on disk")

        manager = SkillManager([tmp_path])
        manager.register(
            Skill(metadata=SkillMetadata(name="fastapi", description="in code"), _content="x")
        )

        assert manager.get("fastapi").metadata.description == "on disk"
        assert manager.names() == ["fastapi"]

    def test_a_missing_skills_directory_is_ignored(self, tmp_path: Path):
        assert SkillManager([tmp_path / "nope"]).all() == []

    def test_the_tool_returns_content_and_where_to_find_it(self, tmp_path: Path):
        skill_dir = _make_skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")

        result = SkillTool(SkillManager([tmp_path])).run({"name": "fastapi"})

        assert "Base directory:" in result
        assert str(skill_dir) in result
        assert "Use dependency injection." in result

    def test_an_unknown_skill_is_not_found(self, tmp_path: Path):
        with pytest.raises(ToolError) as exc:
            SkillTool(SkillManager([tmp_path])).run({"name": "nope"})
        assert exc.value.code == "not_found"

    def test_frontmatter_parsing_is_shared_with_plugins(self):
        fm, body = parse_frontmatter("---\nname: test\ndescription: desc\n---\n\nBody here")
        assert fm == {"name": "test", "description": "desc"}
        assert body == "Body here"
