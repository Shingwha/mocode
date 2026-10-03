"""The shell plugin's background jobs — start, read, kill, and the cleanup.

Claude Code's trio, adapted to mocode's one-subprocess-per-command session:
``bash(run_in_background=true)`` returns a handle at once, ``bash_output``
reads incrementally (reading is consuming; a filter consumes only what it
matched), ``kill_shell`` stops a job. Output waits in bounded rings, a
background deadline is enforced by a watchdog, and everything dies with the
conversation. Foreground behaviour is untouched — those tests live in
``test_tools.py`` and must keep passing unchanged.

Every child here is a *bounded sleep child*: long enough that the job is
still running when a test observes it, short enough that the suite never
spends real time on one. The test side waits on events — the job's own
``done``, an output sink, the turn's end — never on a duration.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from mocode.core.events import (
    PluginMessage,
    ToolCallFinished,
    ToolCallStarted,
    ToolOutput,
)
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
from .conftest import settle
from mocode.testing import call_tool, collect, terminal

BG = {"run_in_background": True}

#: How long a bounded sleep child sleeps — outlives every bound a test
#: observes (a wait timeout, the watchdog deadline, the handle assertion)
#: while costing the suite a fraction of a second.
CHILD = 0.2


@pytest.fixture
def session(tmp_path: Path) -> BashSession:
    return BashSession(tmp_path)


@pytest.fixture
def tools(session: BashSession):
    return bash_tool_for(session), bash_output_tool(session), kill_shell_tool(session)


async def _start(bash, command: str) -> str:
    result = await bash.run_async({"command": command, **BG}, None)
    return result.details["shell_id"]


def _quick_window(monkeypatch, seconds: float) -> None:
    """Compress the plugin's coalescing window for one announcement.

    Only safe where a *single* job is announced — the window is then pure
    latency, not the coalescing rule. The tests that prove the coalescing
    itself (two finishers sharing one entry) keep the product's window,
    because how far apart two spawned children finish is exactly what it
    is meant to absorb."""

    from mocode.host.plugin.builtin.shell import session as shell_session

    monkeypatch.setattr(shell_session, "_NOTIFY_WINDOW", 0.05)


async def _done(session: BashSession, shell_id: str) -> None:
    """Wait for a job to end — on the job's own event, inside a bound."""
    job = session.jobs[shell_id]
    await asyncio.wait_for(job.done.wait(), 5)


class TestBackgroundStart:
    async def test_the_handle_comes_back_first_and_the_job_runs_to_completion(
        self, tools, session
    ):
        """The handle is in hand before the command has produced anything —
        and the job it names then runs to completion.

        Observed, not timed: the sink below fires from the job's collector,
        which cannot have run yet when the start call returns — the task it
        runs in is only created on the way out. A stopwatch could only ever
        guess at that; the unset event is the fact.
        """
        bash, _, _ = tools
        said = asyncio.Event()

        async def watch(text: str, stream: str) -> None:
            said.set()

        result = await session.start_background(
            f"echo up; sleep {CHILD}", on_output=watch
        )

        assert not said.is_set()  # the call came back before the child spoke
        assert result.content == "started shell_1 (running in background)"
        assert result.details == {
            "shell_id": "shell_1",
            "command": f"echo up; sleep {CHILD}",
            "status": "running",
        }
        assert "exit_code" not in result.details
        # And the child really is running: the wait is on its first output,
        # not on an assumed duration.
        await asyncio.wait_for(said.wait(), 5)
        assert session.jobs["shell_1"].running
        # …and it finishes on its own, status and exit code carried.
        await _done(session, "shell_1")
        assert session.jobs["shell_1"].status == "completed"
        assert session.jobs["shell_1"].exit_code == 0


class TestBashOutput:
    async def test_reading_is_consuming_a_filter_takes_matches_and_bad_ones_error(
        self, tools, session
    ):
        bash, output, kill = tools
        # the job's lines arrive after the read below — observed state, not a
        # guess about durations
        shell_id = await _start(bash, "sleep 0.15; echo one; echo ERR bad; echo OK two")

        first = await output.run_async({"shell_id": shell_id}, None)
        assert first.details["lines"] == []
        assert first.content == "(no new output)"
        assert first.details["status"] == "running"
        assert "exit_code" not in first.details or first.details["exit_code"] is None

        # the wait blocks on the job's own done event, and the lines it hands
        # back are then consumed by reading them
        second = await output.run_async({"shell_id": shell_id, "wait": True}, None)
        assert second.details["lines"] == ["one", "ERR bad", "OK two"]
        assert second.details["status"] == "completed"
        assert second.details["exit_code"] == 0

        # reading is consuming — no line comes back twice
        third = await output.run_async({"shell_id": shell_id}, None)
        assert third.details["lines"] == []

        # a filter consumes only what it matched — a job of its own, whose
        # non-matching lines stay buffered for the next unfiltered read
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

        # an unknown shell id is a not-found error — on either tool — and a
        # filter the re module refuses is a parameter error, not a crash
        with pytest.raises(ToolError) as exc:
            await output.run_async({"shell_id": "shell_9"}, None)
        assert exc.value.code == "not_found"
        with pytest.raises(ToolError) as exc:
            await kill.run_async({"shell_id": "shell_9"}, None)
        assert exc.value.code == "not_found"

        shell_id = await _start(bash, "echo hi")
        with pytest.raises(ToolError) as exc:
            await output.run_async({"shell_id": shell_id, "filter": "([a"}, None)
        assert exc.value.code == "invalid_param"
        await session.kill(shell_id)

    async def test_a_wait_blocks_until_done_and_a_bounded_wait_reports_running(
        self, tools, session
    ):
        bash, output, _ = tools
        shell_id = await _start(bash, f"sleep {CHILD}; echo done")

        # A job that outlives the wait fifty times over, so the wait's own
        # bound — a tenth of a second — is the only thing that expires.
        bounded = await output.run_async(
            {"shell_id": shell_id, "wait": True, "timeout": 0.1}, None
        )
        assert bounded.details["status"] == "running"

        # Issue the read first and let it reach its wait; the job is observed
        # still running, so what the read returns below is a blocked-then-
        # completed read, not a guess about durations. One loop turn is all
        # the read needs to park on the job's own done event.
        waiter = asyncio.ensure_future(
            output.run_async({"shell_id": shell_id, "wait": True}, None)
        )
        await settle()  # one loop turn for the read to park on job.done
        assert session.jobs[shell_id].running

        result = await waiter
        assert result.details["lines"] == ["done"]
        assert result.details["status"] == "completed"


class TestTheRings:
    async def test_a_ring_bounds_its_lines_and_bytes_and_a_flood_reports_them(
        self, tools, session
    ):
        # the rule, as a unit: both bounds hold and the dropped lines are
        # counted
        ring = _Ring(max_lines=3)
        for i in range(5):
            ring.append(f"line{i}\n")
        assert [line.strip() for line in ring.drain()] == [
            "line2",
            "line3",
            "line4",
        ]
        assert ring.discarded == 2

        ring = _Ring(max_lines=100, max_bytes=10)
        for text in ("aaaa\n", "bbbb\n", "cccc\n"):
            ring.append(text)
        assert [line.strip() for line in ring.lines] == ["bbbb", "cccc"]
        assert ring.discarded == 1

        # and the same rule end to end: the job's own rings are bounded, and
        # the read reports the count the ring kept
        bash, output, _ = tools

        shell_id = await _start(bash, "for i in $(seq 1 3000); do echo line$i; done")
        await _done(session, shell_id)

        result = await output.run_async({"shell_id": shell_id}, None)
        assert len(result.details["lines"]) == 2000
        assert "(1000 earlier lines discarded)" in result.content
        job = session.jobs[shell_id]
        assert job.buf_out.discarded == 1000


class TestKillAndCleanup:
    async def test_kill_restart_shutdown_and_close_leave_no_job_behind(
        self, tools, session, mc, tmp_path
    ):
        bash, output, kill = tools

        shell_id = await _start(bash, f"sleep {CHILD}")
        result = await kill.run_async({"shell_id": shell_id}, None)

        assert result.content == "killed shell_1"
        assert result.details["status"] == "killed"
        job = session.jobs[shell_id]
        assert job.done.is_set()

        after = await output.run_async({"shell_id": shell_id}, None)
        assert after.details["status"] == "killed"

        # a job that already finished reports that instead of killing twice
        finished = await _start(bash, "echo hi")
        await _done(session, finished)
        result = await kill.run_async({"shell_id": finished}, None)
        assert result.content == "shell_2 already completed"
        assert result.details["status"] == "completed"

        # a restart kills every background job and leaves the table empty
        first = await _start(bash, f"sleep {CHILD}")
        second = await _start(bash, f"sleep {CHILD}")
        jobs = [session.jobs[first], session.jobs[second]]

        result = await bash.run_async({"command": "x", "restart": True}, None)

        assert result.content == "Bash session restarted"
        assert session.jobs == {}
        assert all(job.status == "killed" for job in jobs)

        # …and a shutdown clears whatever the restarted session started
        await _start(bash, f"sleep {CHILD}")
        session.shutdown()

        assert session.jobs == {}

        # the conversation-level close is the same promise through the
        # plugin: what it built dies with it
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")
        session = bash.session

        await bash.run_async({"command": f"sleep {CHILD}", **BG}, None)
        await bash.run_async({"command": f"sleep {CHILD}", **BG}, None)
        jobs = list(session.jobs.values())
        assert len(jobs) == 2

        conversation.close(save=False)
        assert session.jobs == {}
        assert all(job.status == "killed" for job in jobs)
        # The collectors mop up after the kills — a killed child's pipes get
        # a short grace on Windows, so done arrives within a bound, not at
        # once. Wait on the jobs' own events, not on a guessed grace period.
        await asyncio.wait_for(
            asyncio.gather(*(job.done.wait() for job in jobs)), 5.0
        )


class TestLimits:
    async def test_the_background_cap_and_its_configuration(self, mc, tmp_path: Path):
        mc.config.plugins["shell"] = {"max_background": 2}
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        await _start(bash, f"sleep {CHILD}")
        await _start(bash, f"sleep {CHILD}")
        with pytest.raises(ToolError) as exc:
            await bash.run_async({"command": f"sleep {CHILD}", **BG}, None)
        assert exc.value.code == "limit"
        assert "limit 2" in exc.value.message
        session = bash.session
        await session.kill("shell_1")
        await session.kill("shell_2")

        # the configuration itself: a shape the session cannot use is ignored
        # and the default stands, a usable one is applied
        session = BashSession(tmp_path)
        session.configure({"max_background": "many", "background_timeout": -5})
        assert session.max_background == 16
        assert session.background_timeout == 3600
        session.configure({"max_background": 3, "background_timeout": 0})
        assert (session.max_background, session.background_timeout) == (3, 0)

    async def test_a_background_deadline_times_the_job_out(
        self, mc, tmp_path: Path, monkeypatch
    ):
        _quick_window(monkeypatch, 0.05)
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        # The deadline is the subject, so keep one real-time integration: the
        # watchdog gets a tenth of a second to stop a job that could not
        # otherwise end for half a second, and the wait is on the job's done
        # event.
        result = await bash.run_async(
            {"command": f"sleep {CHILD}", "timeout": 0.1, **BG}, None
        )
        session = bash.session
        job = session.jobs[result.details["shell_id"]]

        await asyncio.wait_for(job.done.wait(), 5)
        assert job.status == "timed_out"

        # and the watchdog's kill is announced like any other finish
        reader = conversation.agent.channel.subscribe(
            since=0, keep=lambda event: isinstance(event, PluginMessage)
        )
        message = await asyncio.wait_for(reader.get(), 5)
        assert message.kind == "shell/background-done"
        assert message.data["jobs"][0]["status"] == "timed_out"
        conversation.close(save=False)


class TestSessionSemantics:
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
    async def test_a_jobs_first_lines_reach_the_open_block_of_its_start_call(
        self, wired, tmp_path: Path
    ):
        """Early output rides the existing ToolOutput mechanism into the block
        of the call that started the job — and the ring keeps it too: the
        event is a report, not a consumption."""
        conversation, _ = wired(
            call_tool("bash", {"command": f"echo early; sleep {CHILD}", **BG}),
            call_tool("bash", {"command": "echo second"}, call_id="c2"),
            "done",
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

    async def _messages(self, conversation, *, count: int, timeout: float = 5.0):
        """The next *count* completion announcements — read, not polled.

        The channel replays its history to a fresh subscriber, so this is
        correct whether the announcement already landed or is still inside
        the coalescing window; each read either gets the entry or lets the
        bound expire. Waiting beats polling the history: no sleep cadence,
        no deadline arithmetic, no wall-clock cost while nothing happens.
        """
        reader = conversation.agent.channel.subscribe(
            since=0, keep=lambda event: isinstance(event, PluginMessage)
        )
        found: list[PluginMessage] = []
        for _ in range(count):
            try:
                found.append(await asyncio.wait_for(reader.get(), timeout))
            except asyncio.TimeoutError:
                break
        return found

    async def test_a_burst_of_finishers_announces_as_one_block(
        self, mc, tmp_path: Path
    ):
        """The two sides of the coalescing window: same-moment finishers
        share one announcement, a later burst gets a block of its own."""
        conversation = mc.new_conversation(cwd=tmp_path)
        bash = conversation.tools.get("bash")

        # The two jobs carry the same duration and are started back to back,
        # so they finish inside one coalescing window; the assertion below
        # (one announcement, listing both) is what makes that fail loudly if
        # they ever drift apart instead of passing quietly.
        await bash.run_async({"command": f"sleep 0.15; echo a", **BG}, None)
        await bash.run_async({"command": f"sleep 0.15; echo b", **BG}, None)

        messages = await self._messages(conversation, count=1)
        assert len(messages) == 1, "two same-moment finishers, one announcement"
        message = messages[0]
        assert message.kind == "shell/background-done"
        assert message.block_id == "shell-bg-1"
        assert message.run_id == ""  # said between turns, to whoever is watching
        assert [job["id"] for job in message.data["jobs"]] == ["shell_1", "shell_2"]

        # The merge is deterministic: start order, numeric — not completion
        # order (a scheduling accident) and not lexicographic (shell_10
        # would sort before shell_2).
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
        assert all(job["status"] == "completed" for job in message.data["jobs"])
        assert all(job["exit_code"] == 0 for job in message.data["jobs"])
        assert "echo a" in message.data["jobs"][0]["command"]

        # A burst separated by an observed event — the first announcement
        # arrived before the next job even starts — is its own block, not a
        # hoped-for gap between two sleeps.
        await bash.run_async({"command": f"sleep {CHILD}", **BG}, None)
        messages = await self._messages(conversation, count=2)
        assert [m.block_id for m in messages] == ["shell-bg-1", "shell-bg-2"]
        conversation.close(save=False)

    async def test_the_announcement_waits_for_idle_and_a_killed_job_is_never_told(
        self, wired, tmp_path: Path
    ):
        """The model reads what it started; the announcement waits for idle —
        its empty run_id proves it was said between turns. A job the user
        killed is not something the model needs told at all.

        The silence is proved on a *fact*, not on a wall clock: the kill is
        the last thing that happens to this job, so the observable set after
        it is the turn end (say 回包到达) with no PluginMessage in it. Waiting
        a window longer would only prove the window, not the behaviour.
        """
        # the idle half: the job finishes inside the foreground call's
        # running time — a comfortable margin — so what is held back is the
        # announcement, not the job.
        idle_conversation, _ = wired(
            call_tool("bash", {"command": "sleep 0.3"}), "done"
        )
        idle_bash = idle_conversation.tools.get("bash")
        await idle_bash.run_async({"command": "sleep 0.15", **BG}, None)
        await collect(idle_conversation.stream("go"))

        messages = await self._messages(idle_conversation, count=1)
        assert len(messages) == 1
        assert messages[0].run_id == ""
        idle_conversation.close(save=False)

        # the kill half: the kill is the last thing that happens to the job,
        # so the observable set after it is the turn end with no announcement
        conversation, _ = wired(
            call_tool("bash", {"command": f"echo up; sleep {CHILD}", **BG}),
            call_tool(
                "kill_shell",
                {"shell_id": "shell_1"},
                call_id="k1",
            ),
            "done",
        )
        events = await collect(conversation.stream("go"))

        # the turn ended — the model answered — so nothing was held back
        assert terminal(events).content == "done"
        # and the job's whole life is in that turn's events: no announcement
        assert [e for e in events if isinstance(e, PluginMessage)] == []
        killed = [
            e
            for e in events
            if isinstance(e, ToolCallFinished) and e.name == "kill_shell"
        ]
        assert [e.status for e in killed] == ["ok"]
        conversation.close(save=False)


class TestTerminate:
    """The process-group kill — the Windows branch runs here for real; the
    POSIX branch is exercised against a stubbed platform."""

    async def test_windows_kills_the_direct_child(self, tools, session):
        if sys.platform != "win32":
            pytest.skip("the Windows branch of _terminate")
        bash, _, _ = tools

        shell_id = await _start(bash, f"sleep {CHILD}")
        job = session.jobs[shell_id]
        _terminate(job.proc)
        await asyncio.wait_for(job.proc.wait(), 5)
        assert job.proc.returncode is not None

    def test_posix_kills_the_group_then_falls_back(self, monkeypatch):
        calls: list[tuple[int, int]] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append((pgid, sig))

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)

        class Proc:
            def __init__(self, *, pid: int = 4321, returncode=None):
                self.pid = pid
                self.returncode = returncode
                self.killed = False

            def kill(self) -> None:
                self.killed = True

        # a finished process is left alone — before any group is consulted
        finished = Proc(returncode=0)
        _terminate(finished)  # type: ignore[arg-type]
        assert finished.killed is False and calls == []

        # the group is the first target, and the child is spared
        leader = Proc()
        _terminate(leader)  # type: ignore[arg-type]
        assert calls == [(4321, _SIGKILL)]
        assert leader.killed is False

        # the group already gone: the direct kill is the fallback
        def gone(pgid: int, sig: int) -> None:
            raise ProcessLookupError()

        monkeypatch.setattr(os, "killpg", gone, raising=False)
        orphan = Proc()
        _terminate(orphan)  # type: ignore[arg-type]
        assert orphan.killed is True
