"""The builtin tools: bash, read, and the skill lookup."""

from __future__ import annotations

from pathlib import Path

import pytest

from mocode.host.plugin.builtin.filesystem import read_tool
from mocode.host.plugin.builtin.shell import BashSession, bash_tool
from mocode.host.plugin.builtin.skills import (
    Skill,
    SkillManager,
    SkillMetadata,
    skill_tool,
)
from mocode.host.plugin.builtin.skills import parse_frontmatter
from mocode.core import ToolError, ToolRegistry

from .conftest import skill_dir


class TestBashSession:
    async def test_a_command_runs_and_its_exit_code_travels(self, tmp_path: Path):
        session = BashSession(tmp_path)
        assert (await session.execute("echo hello", timeout=10)).content == "hello"
        assert (await session.execute("true", timeout=10)).details == {"exit_code": 0}
        assert (await session.execute("exit 3", timeout=10)).details == {"exit_code": 3}

    async def test_state_persists_and_a_restart_clears_it(self, tmp_path: Path):
        session = BashSession(tmp_path)
        # the working directory and the environment are session state: both
        # survive a command and both are cleared by a restart
        result = await session.execute(f"cd {tmp_path}", timeout=10)
        assert str(tmp_path) in result.content
        assert session.cwd == str(tmp_path)
        await session.execute("export MY_TEST_VAR=world", timeout=10)
        assert (await session.execute("echo $MY_TEST_VAR", timeout=10)).content == "world"

        session.restart()
        assert (await session.execute("echo $MY_TEST_VAR", timeout=10)).content == "(empty)"

        # the same state through the tool, and the restart argument clears it
        tool = bash_tool(tmp_path)
        await tool.run_async({"command": "export V=1"}, None)
        assert (await tool.run_async({"command": "echo $V"}, None)).content == "1"
        assert (await tool.run_async({"command": "x", "restart": True}, None)).content == (
            "Bash session restarted"
        )
        assert (await tool.run_async({"command": "echo $V"}, None)).content == "(empty)"

    async def test_env_values_are_never_executed_as_shell_code(self, tmp_path: Path):
        """Env vars are passed through ``env=``, never interpolated into a script."""
        session = BashSession(tmp_path)
        await session.execute("export EVIL='$(echo INJECTED)'", timeout=10)
        await session.execute("export TICK='`echo INJECTED`'", timeout=10)

        assert (await session.execute("echo $EVIL", timeout=10)).content == "$(echo INJECTED)"
        assert (await session.execute("echo $TICK", timeout=10)).content == "`echo INJECTED`"

    async def test_output_is_reported_line_by_line_as_it_arrives(self, tmp_path: Path):
        seen: list[tuple[str, str]] = []

        async def on_output(text: str, stream: str) -> None:
            seen.append((stream, text))

        result = await BashSession(tmp_path).execute(
            "echo one; echo two; echo oops >&2", timeout=10, on_output=on_output
        )

        assert seen == [
            ("stdout", "one\n"),
            ("stdout", "two\n"),
            ("stderr", "oops\n"),
        ]
        assert "one" in result.content and "oops" in result.content

    async def test_timeout_kills_the_command_at_both_levels(self, tmp_path: Path):
        # the session's own budget: the command dies at the deadline, with
        # the number read out of the message
        result = await BashSession(tmp_path).execute("sleep 5", timeout=0.3)
        # the timeout is the subject — what is asserted is that the command
        # died at the deadline, with the number read out of the message
        assert result.content.startswith("(timed out after ")
        assert result.content.endswith("s)")
        assert float(result.content.rsplit(" ", 1)[1][:-2]) == 0.3
        assert "exit_code" not in result.details

        # and the model's timeout argument reaches the dispatcher, which is
        # the one place the deadline is enforced
        from mocode.core.agent import AgentConfig
        from mocode.core.dispatch import ToolDispatcher
        from mocode.core.events import Event
        from mocode.core.hook import HookRunner

        async def publish(event: Event, *, fold: bool) -> None:
            pass

        tool = bash_tool(tmp_path)
        registry = ToolRegistry()
        registry.register(tool)
        dispatcher = ToolDispatcher(
            registry, HookRunner(), AgentConfig(tool_timeout=30), publish
        )

        result = await dispatcher.run("bash", {"command": "sleep 5", "timeout": 0.2})

        assert result.status == "timeout"
        assert result.content.startswith("timeout:")


class TestBashTool:
    def test_the_tool_shape_and_its_timeout_policy(self, tmp_path: Path):
        from mocode.core.tool import ToolPolicy

        tool = bash_tool(tmp_path)
        assert tool.wants_context is True
        assert tool.is_async is True
        assert tool.result_key == "exit_code"
        # the dispatcher enforces the deadline; the tool just maps the
        # argument onto a ToolPolicy and reads the resolved value back
        assert tool.policy({"timeout": 7}) == ToolPolicy(timeout=7)
        assert tool.policy({}) == ToolPolicy(timeout=None)  # fall to config


class TestReadTool:
    def test_a_directory_listing_counts_its_kinds_and_skips_the_noise(
        self, tmp_path: Path
    ):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "hello.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "real.py").write_text("x", encoding="utf-8")

        result = read_tool(tmp_path).run({"path": str(tmp_path)}).content

        assert result.startswith("[")
        # the listing is a set of entries; the summary line is the user's
        # wording, so what a test holds is that both kinds are counted — the
        # noise directory counted in neither
        summary = result.split("\n", 1)[0]
        assert "subdir/" in result and "hello.py" in result and "real.py" in result
        assert "1" in summary and "2" in summary
        assert "directories" in summary and "files" in summary
        # the way out of a listing is named, and no line count is shown for
        # what has none
        assert "directory" in result.lower()
        assert "shell" in result
        assert read_tool(tmp_path).run({"path": str(tmp_path)}).details == {}
        # the noise directories never make the listing
        assert "__pycache__" not in result

        # an empty directory is reported, counted as none of either
        empty = tmp_path / "empty"
        empty.mkdir()
        result = read_tool(empty).run({"path": str(empty)}).content
        summary = result.split("\n", 1)[0]
        assert "0" in summary and "directories" in summary and "files" in summary

    def test_a_file_read_reports_its_line_numbers(self, tmp_path: Path):
        path = tmp_path / "a.py"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = read_tool(tmp_path).run({"path": str(path)})

        assert result.details == {"lines": 3, "total_lines": 3}
        assert "one" in result.content

        # a partial read reports both its own count and the file's
        path = tmp_path / "big.py"
        path.write_text("\n".join(str(i) for i in range(50)), encoding="utf-8")
        partial = read_tool(tmp_path).run({"path": str(path), "offset": 1, "limit": 10})
        assert partial.details == {"lines": 10, "total_lines": 50}


class TestSkills:
    def test_a_skill_loads_its_body_and_a_nameless_one_is_skipped(self, tmp_path):
        path = skill_dir(tmp_path, "my-skill", "test", "Hello world\n")

        skill = Skill.from_dir(path)

        assert skill.load_content() == "Hello world"
        assert skill.base_dir == str(path)
        # frontmatter the schema does not name survives on the metadata
        meta = SkillMetadata.from_dict({"name": "x", "description": "d", "version": "1"})
        assert (meta.name, meta.description, meta.attrs) == ("x", "d", {"version": "1"})

        # a skill whose frontmatter names no skill at all is skipped
        nameless = tmp_path / "nameless"
        nameless.mkdir()
        (nameless / "SKILL.md").write_text("---\ndescription: no name\n---\n", encoding="utf-8")
        assert Skill.from_dir(nameless) is None

    def test_the_manager_prefers_the_directory_and_the_tool_returns_content(
        self, tmp_path
    ):
        skill_dir(tmp_path, "fastapi", "on disk")

        manager = SkillManager([tmp_path])
        manager.register(
            Skill(metadata=SkillMetadata(name="fastapi", description="in code"), _content="x")
        )

        assert manager.get("fastapi").metadata.description == "on disk"
        assert manager.names() == ["fastapi"]
        assert SkillManager([tmp_path / "nope"]).all() == []

        # the tool hands the body back, and an unknown name is a not-found
        path = skill_dir(tmp_path, "fastapi", "FastAPI tips", "Use dependency injection.")

        result = skill_tool(SkillManager([tmp_path])).run({"name": "fastapi"})

        assert str(path) in result
        assert "Use dependency injection." in result
        assert parse_frontmatter("---\nname: test\ndescription: desc\n---\n\nBody here") == (
            {"name": "test", "description": "desc"},
            "Body here",
        )

        with pytest.raises(ToolError) as exc:
            skill_tool(SkillManager([tmp_path])).run({"name": "nope"})
        assert exc.value.code == "not_found"
