"""The shell plugin's background jobs — start, read, kill, and the cleanup.

Claude Code's trio, adapted to mocode's one-subprocess-per-command session:
``bash(run_in_background=true)`` returns a handle at once, ``bash_output``
reads incrementally (reading is consuming; a filter consumes only what it
matched), ``kill_shell`` stops a job. Output waits in bounded rings, a
background deadline is enforced by a watchdog, and everything dies with the
conversation. Foreground behaviour is untouched — those tests live in
``test_tools.py`` and must keep passing unchanged.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from mocode.core.events import PluginMessage, ToolCallStarted, ToolOutput
from mocode.core.tool import ToolError
from mocode.host.plugin.builtin.shell import (
    _Ring,
    _SIGKILL,
    _terminate,
    bash_tool_for,
    bash_output_tool,
    kill_shell_tool,
)
from mocode.host.plugin.builtin.shell import BashSession
from mocode.testing import MockProvider, call_tool, collect, say

BG = {"run_in_background": True}


@pytest.fixture
def session(tmp_path: Path) -> BashSession:
    return BashSession(tmp_path)


@pytest.fixture
def tools(session: BashSession):
    return bash_tool_for(session), bash_output_tool(session), kill_shell_tool(session)


async def _start(bash, command: str) -> str:
    result = await bash.run_async({"command": command, **BG}, None)
    return result.details["shell_id"]


async def _done(session: BashSession, shell_id: str) -> None:
    job = session.jobs[shell_id]
    await asyncio.wait_for(job.done.wait(), 10)


class TestBackgroundStart:
    @pytest.mark.asyncio
    async def test_returns_a_handle_immediately(self, tools, session):
        bash, _, _ = tools

        started = asyncio.get_event_loop().time()
        result = await bash.run_async({"command": "sleep 1", **BG}, None)
        elapsed = asyncio.get_event_loop().time() - started

        assert elapsed < 0.8
        assert result.content == "started shell_1 (running in background)"
        assert result.details == {
            "shell_id": "shell_1",
            "command": "sleep 1",
            "status": "running",
        }
        assert "exit_code" not in result.details
        await session.kill("shell_1")

    @pytest.mark.asyncio
    async def test_the_job_runs_and_finishes(self, tools, session):
        bash, _, _ = tools

        shell_id = await _start(bash, "echo hi")

        await _done(session, shell_id)
        job = session.jobs[shell_id]
        assert job.status == "completed"
        assert job.exit_code == 0


class TestBashOutput:
    @pytest.mark.asyncio
    async def test_reading_is_consuming_no_line_comes_back_twice(
        self, tools, session
    ):
        bash, output, _ = tools

        shell_id = await _start(bash, "sleep 0.4; echo a; echo b")

        first = await output.run_async({"shell_id": shell_id}, None)
        assert first.details["lines"] == []
        assert first.content == "(no new output)"
        assert first.details["status"] == "running"
        assert "exit_code" not in first.details or first.details["exit_code"] is None

        second = await output.run_async({"shell_id": shell_id, "wait": True}, None)
        assert second.details["lines"] == ["a", "b"]
        assert second.details["status"] == "completed"
        assert second.details["exit_code"] == 0

        third = await output.run_async({"shell_id": shell_id}, None)
        assert third.details["lines"] == []
        assert third.content == "(no new output)"

    @pytest.mark.asyncio
    async def test_a_filter_consumes_only_the_matching_lines(self, tools, session):
        bash, output, _ = tools

        shell_id = await _start(
            bash, "echo ERR bad; echo OK one; echo ERR worse; echo OK two"
        )
        await _done(session, shell_id)

        matched = await output.run_async(
            {"shell_id": shell_id, "filter": "OK"}, None
        )
        assert matched.details["lines"] == ["OK one", "OK two"]

        # The matches were consumed; the non-matching lines stayed buffered.
        again = await output.run_async({"shell_id": shell_id, "filter": "OK"}, None)
        assert again.details["lines"] == []
        rest = await output.run_async({"shell_id": shell_id}, None)
        assert rest.details["lines"] == ["ERR bad", "ERR worse"]

    @pytest.mark.asyncio
    async def test_a_bad_filter_regex_is_a_tool_error(self, tools, session):
        bash, output, _ = tools

        shell_id = await _start(bash, "echo hi")
        with pytest.raises(ToolError) as exc:
            await output.run_async({"shell_id": shell_id, "filter": "([a"}, None)
        assert exc.value.code == "invalid_param"
        await session.kill(shell_id)

    @pytest.mark.asyncio
    async def test_wait_blocks_until_the_job_completes(self, tools, session):
        bash, output, _ = tools

        shell_id = await _start(bash, "sleep 0.3; echo done")

        result = await output.run_async({"shell_id": shell_id, "wait": True}, None)
        assert result.details["lines"] == ["done"]
        assert result.details["status"] == "completed"

    @pytest.mark.asyncio
    async def test_wait_with_a_timeout_reports_the_still_running_job(
        self, tools, session
    ):
        bash, output, _ = tools

        shell_id = await _start(bash, "sleep 5")

        result = await output.run_async(
            {"shell_id": shell_id, "wait": True, "timeout": 0.3}, None
        )
        assert result.details["status"] == "running"
        await session.kill(shell_id)

    @pytest.mark.asyncio
    async def test_an_unknown_shell_is_not_found(self, tools):
        _, output, kill = tools
        with pytest.raises(ToolError) as exc:
            await output.run_async({"shell_id": "shell_9"}, None)
        assert exc.value.code == "not_found"
        with pytest.raises(ToolError) as exc:
            await kill.run_async({"shell_id": "shell_9"}, None)
        assert exc.value.code == "not_found"


class TestTheRings:
    def test_a_ring_bounds_by_lines_and_counts_the_dropped(self):
        ring = _Ring(max_lines=3)
        for i in range(5):
            ring.append(f"line{i}\n")
        assert [line.strip() for line in ring.drain()] == [
            "line2",
            "line3",
            "line4",
        ]
        assert ring.discarded == 2

    def test_a_ring_bounds_by_bytes_too(self):
        ring = _Ring(max_lines=100, max_bytes=10)
        for text in ("aaaa\n", "bbbb\n", "cccc\n"):
            ring.append(text)
        assert [line.strip() for line in ring.lines] == ["bbbb", "cccc"]
        assert ring.discarded == 1

    def test_a_filtered_take_keeps_the_unmatched(self):
        import re

        ring = _Ring()
        for text in ("one\n", "two\n", "three\n"):
            ring.append(text)
        matched = ring.take_matching(re.compile("t.o"))
        assert [line.strip() for line in matched] == ["two"]
        assert [line.strip() for line in ring.lines] == ["one", "three"]
        assert ring.discarded == 0

    @pytest.mark.asyncio
    async def test_a_flooded_job_reports_what_the_ring_dropped(self, tools, session):
        bash, output, _ = tools

        shell_id = await _start(bash, "for i in $(seq 1 3000); do echo line$i; done")
        await _done(session, shell_id)

        result = await output.run_async({"shell_id": shell_id}, None)
        assert len(result.details["lines"]) == 2000
        assert "(1000 earlier lines discarded)" in result.content
        job = session.jobs[shell_id]
        assert job.buf_out.discarded == 1000


class TestKillAndCleanup:
    @pytest.mark.asyncio
    async def test_kill_shell_stops_the_job(self, tools, session):
        bash, output, kill = tools

        shell_id = await _start(bash, "sleep 5")
        result = await kill.run_async({"shell_id": shell_id}, None)

        assert result.content == "killed shell_1"
        assert result.details["status"] == "killed"
        job = session.jobs[shell_id]
        assert job.done.is_set()

        after = await output.run_async({"shell_id": shell_id}, None)
        assert after.details["status"] == "killed"

    @pytest.mark.asyncio
    async def test_killing_a_finished_job_reports_its_status(self, tools, session):
        bash, _, kill = tools

        shell_id = await _start(bash, "echo hi")
        await _done(session, shell_id)

        result = await kill.run_async({"shell_id": shell_id}, None)
        assert result.content == "shell_1 already completed"
        assert result.details["status"] == "completed"

    @pytest.mark.asyncio
    async def test_restart_kills_every_background_job(self, tools, session):
        bash, _, _ = tools

        first = await _start(bash, "sleep 5")
        second = await _start(bash, "sleep 5")
        jobs = [session.jobs[first], session.jobs[second]]

        result = await bash.run_async({"command": "x", "restart": True}, None)

        assert result.content == "Bash session restarted"
        assert session.jobs == {}
        assert all(job.status == "killed" for job in jobs)

    @pytest.mark.asyncio
    async def test_shutdown_clears_the_jobs(self, tools, session):
        bash, _, _ = tools

        await _start(bash, "sleep 5")
        await _start(bash, "sleep 5")
        session.shutdown()

        assert session.jobs == {}

    @pytest.mark.asyncio
    async def test_the_plugins_close_kills_what_it_built(self, mc, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")
        session = bash.session

        await bash.run_async({"command": "sleep 5", **BG}, None)
        await bash.run_async({"command": "sleep 5", **BG}, None)
        jobs = list(session.jobs.values())
        assert len(jobs) == 2

        conversation.close(save=False)
        assert session.jobs == {}
        assert all(job.status == "killed" for job in jobs)
        # The collectors mop up after the kills — a killed child's pipes get
        # a short grace on Windows, so done arrives within a bound, not at once.
        deadline = asyncio.get_event_loop().time() + 3.0
        while not all(job.done.is_set() for job in jobs):
            assert asyncio.get_event_loop().time() < deadline, "jobs never ended"
            await asyncio.sleep(0.05)


class TestLimits:
    @pytest.mark.asyncio
    async def test_the_concurrency_cap_rejects_new_background_jobs(
        self, tools, session
    ):
        bash, _, _ = tools
        session.configure({"max_background": 2})

        await _start(bash, "sleep 2")
        await _start(bash, "sleep 2")
        with pytest.raises(ToolError) as exc:
            await bash.run_async({"command": "sleep 2", **BG}, None)
        assert exc.value.code == "limit"
        assert "limit 2" in exc.value.message
        await session.kill("shell_1")
        await session.kill("shell_2")

    @pytest.mark.asyncio
    async def test_the_cap_comes_from_the_plugin_config(self, mc, tmp_path: Path):
        mc.config.plugins["shell"] = {"max_background": 1}
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        first = await bash.run_async({"command": "sleep 2", **BG}, None)
        assert first.details["shell_id"] == "shell_1"
        with pytest.raises(ToolError) as exc:
            await bash.run_async({"command": "sleep 2", **BG}, None)
        assert exc.value.code == "limit"
        session = bash.session
        await session.kill("shell_1")

    @pytest.mark.asyncio
    async def test_a_background_deadline_times_the_job_out(self, tools, session):
        bash, output, _ = tools

        shell_id = await _start(bash, "sleep 5")
        # Re-start with a short explicit deadline instead: kill the first.
        await session.kill(shell_id)
        short = await bash.run_async(
            {"command": "sleep 5", "timeout": 1, **BG}, None
        )
        job = session.jobs[short.details["shell_id"]]

        await asyncio.wait_for(job.done.wait(), 10)
        assert job.status == "timed_out"

    @pytest.mark.asyncio
    async def test_configure_ignores_bad_values(self, session):
        session.configure({"max_background": "many", "background_timeout": -5})
        assert session.max_background == 16
        assert session.background_timeout == 3600
        session.configure({"max_background": 3, "background_timeout": 0})
        assert (session.max_background, session.background_timeout) == (3, 0)


class TestSessionSemantics:
    @pytest.mark.asyncio
    async def test_a_job_snapshots_cwd_and_env_at_start(self, tools, session, tmp_path):
        bash, output, _ = tools
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        await bash.run_async({"command": f"cd {elsewhere}"}, None)
        await bash.run_async({"command": "export V=first"}, None)
        shell_id = await _start(bash, "pwd; echo $V")
        await bash.run_async({"command": f"cd {tmp_path}"}, None)
        await bash.run_async({"command": "export V=second"}, None)

        result = await output.run_async({"shell_id": shell_id, "wait": True}, None)
        lines = result.details["lines"]
        assert "elsewhere" in lines[0].replace("\\", "/")
        assert lines[1] == "first"


class TestEarlyOutput:
    @pytest.mark.asyncio
    async def test_a_jobs_first_lines_reach_the_open_block_of_its_start_call(
        self, mc, tmp_path: Path
    ):
        """Early output rides the existing ToolOutput mechanism into the block
        of the call that started the job — and the ring keeps it too: the
        event is a report, not a consumption."""
        conversation = mc.new_conversation(cwd=tmp_path)
        conversation.agent.provider = MockProvider(
            [
                call_tool(
                    "bash", {"command": "echo early; sleep 1", **BG}
                ),
                call_tool("bash", {"command": "sleep 0.3"}),
                say("done"),
            ]
        )

        events = await collect(conversation.stream("go"))

        started = next(
            e
            for e in events
            if isinstance(e, ToolCallStarted)
            and e.name == "bash"
            and "early" in str(e.args.get("command", ""))
        )
        outputs = [
            e for e in events if isinstance(e, ToolOutput) and e.call_id == started.call_id
        ]
        assert [o.text for o in outputs] == ["early\n"]

        output_tool = conversation.tools.get("bash_output")
        read = await output_tool.run_async({"shell_id": "shell_1"}, None)
        assert read.details["lines"] == ["early"]

        kill_tool = conversation.tools.get("kill_shell")
        await kill_tool.run_async({"shell_id": "shell_1"}, None)
        conversation.close(save=False)


class TestCompletionNotification:
    """Jobs that end on their own announce it — once, together, when idle."""

    def test_the_announcement_lists_jobs_in_start_order(self):
        """The merge is deterministic: start order, numeric — not completion
        order (a scheduling accident) and not lexicographic (shell_10 would
        sort before shell_2)."""
        from mocode.host.plugin.builtin.shell import _start_order

        class Stub:
            def __init__(self, id: str):
                self.id = id

        shuffled = [Stub(i) for i in ("shell_10", "shell_2", "shell_1", "shell_9")]
        assert [s.id for s in sorted(shuffled, key=_start_order)] == [
            "shell_1",
            "shell_2",
            "shell_9",
            "shell_10",
        ]

    async def _messages(self, conversation, *, count: int, timeout: float = 5.0):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            found = [
                e
                for e in conversation.agent.channel.history()
                if isinstance(e, PluginMessage)
            ]
            if len(found) >= count:
                return found
            await asyncio.sleep(0.05)
        return [
            e
            for e in conversation.agent.channel.history()
            if isinstance(e, PluginMessage)
        ]

    @pytest.mark.asyncio
    async def test_jobs_finishing_together_announce_as_one(
        self, mc, tmp_path: Path
    ):
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        await bash.run_async({"command": "sleep 0.4; echo a", **BG}, None)
        await bash.run_async({"command": "sleep 0.4; echo b", **BG}, None)

        messages = await self._messages(conversation, count=1)
        assert len(messages) == 1, "two same-moment finishers, one announcement"
        message = messages[0]
        assert message.kind == "shell/background-done"
        assert message.block_id == "shell-bg-1"
        assert message.run_id == ""  # said between turns, to whoever is watching
        assert [job["id"] for job in message.data["jobs"]] == ["shell_1", "shell_2"]
        assert all(job["status"] == "completed" for job in message.data["jobs"])
        assert all(job["exit_code"] == 0 for job in message.data["jobs"])
        assert "sleep 0.4; echo a" in message.data["jobs"][0]["command"]
        conversation.close(save=False)

    @pytest.mark.asyncio
    async def test_each_burst_is_its_own_block(self, mc, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        first = await bash.run_async({"command": "sleep 0.3", **BG}, None)
        messages = await self._messages(conversation, count=1)
        assert len(messages) == 1 and messages[0].block_id == "shell-bg-1"

        await bash.run_async({"command": "sleep 0.3", **BG}, None)
        messages = await self._messages(conversation, count=2)
        assert [m.block_id for m in messages] == ["shell-bg-1", "shell-bg-2"]
        conversation.close(save=False)

    @pytest.mark.asyncio
    async def test_no_announcement_while_a_turn_is_running(self, mc, tmp_path: Path):
        """The model reads what it started; the announcement waits for idle —
        its empty run_id proves it was said between turns."""
        conversation = mc.new_conversation(cwd=tmp_path)
        conversation.agent.provider = MockProvider(
            [call_tool("bash", {"command": "sleep 1"}), say("done")]
        )
        bash = conversation.tools.get("bash")

        await bash.run_async({"command": "sleep 0.3", **BG}, None)
        await collect(conversation.stream("go"))

        messages = await self._messages(conversation, count=1)
        assert len(messages) == 1
        assert messages[0].run_id == ""
        conversation.close(save=False)

    @pytest.mark.asyncio
    async def test_a_killed_job_is_not_announced(self, mc, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")
        kill = conversation.tools.get("kill_shell")

        shell_id = await _start(bash, "sleep 5")
        await kill.run_async({"shell_id": shell_id}, None)
        await asyncio.sleep(3 * 0.3 + 0.2)  # past the coalescing window

        messages = [
            e
            for e in conversation.agent.channel.history()
            if isinstance(e, PluginMessage)
        ]
        assert messages == []
        conversation.close(save=False)

    @pytest.mark.asyncio
    async def test_a_timed_out_job_is_announced(self, mc, tmp_path: Path):
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        await bash.run_async(
            {"command": "sleep 5", "timeout": 1, **BG}, None
        )

        messages = await self._messages(conversation, count=1, timeout=5.0)
        assert len(messages) == 1
        job = messages[0].data["jobs"][0]
        assert job["status"] == "timed_out"
        conversation.close(save=False)


class TestTerminate:
    """The process-group kill — the Windows branch runs here for real; the
    POSIX branch is exercised against a stubbed platform."""

    @pytest.mark.asyncio
    async def test_windows_kills_the_direct_child(self, tools, session):
        if sys.platform != "win32":
            pytest.skip("the Windows branch of _terminate")
        bash, _, _ = tools

        shell_id = await _start(bash, "sleep 5")
        job = session.jobs[shell_id]
        _terminate(job.proc)
        await asyncio.wait_for(job.proc.wait(), 10)
        assert job.proc.returncode is not None

    def test_posix_kills_the_whole_group(self, monkeypatch):
        calls: list[tuple[int, int]] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append((pgid, sig))

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)

        class Proc:
            pid = 4321
            returncode = None
            killed = False

            def kill(self) -> None:
                self.killed = True

        proc = Proc()
        _terminate(proc)  # type: ignore[arg-type]
        assert calls == [(4321, _SIGKILL)]
        assert proc.killed is False

    def test_posix_falls_back_to_the_child_when_the_group_is_gone(
        self, monkeypatch
    ):
        def gone(pgid: int, sig: int) -> None:
            raise ProcessLookupError()

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "killpg", gone, raising=False)

        class Proc:
            pid = 4321
            returncode = None
            killed = False

            def kill(self) -> None:
                self.killed = True

        proc = Proc()
        _terminate(proc)  # type: ignore[arg-type]
        assert proc.killed is True

    def test_a_finished_process_is_left_alone(self):
        class Proc:
            pid = 1
            returncode = 0
            killed = False

            def kill(self) -> None:
                self.killed = True

        proc = Proc()
        _terminate(proc)  # type: ignore[arg-type]
        assert proc.killed is False
