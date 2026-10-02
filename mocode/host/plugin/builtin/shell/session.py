"""The persistent bash session — foreground execution and background jobs.

The session is asynchronous so output can be reported as it is produced: a
command that takes a minute is watchable rather than a blank wait, and a
timeout can actually kill the child process instead of abandoning a thread.

Commands run either in the **foreground** — :meth:`BashSession.execute`
blocks until the command is done, streaming each line as it lands — or in
the **background**, where :meth:`BashSession.start_background` returns a
handle immediately (``shell_1``) and :meth:`BashSession.read_output` reads
incrementally, each read returning only what arrived since the last one.
Background output accumulates in bounded rings, so a dev server that runs
overnight cannot grow memory without bound; what the rings dropped is
counted and reported.

Every command runs in its own process group (POSIX), so a timeout or an
explicit kill takes down the whole tree — ``bash -c "npm run dev"`` must
not leave its node children behind. On Windows there are no process groups:
the direct child is killed, and grandchildren survive it (a known
limitation).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from .....core.tool import ToolError, ToolResult
from ....text import decode_bytes
from ...context import BuildContext
from .ring import _Ring

if TYPE_CHECKING:
    from ...context import HostContext

#: Called with each chunk of output as it arrives: ``on_output(text, stream)``.
OutputSink = Callable[[str, str], Awaitable[None]]

#: POSIX's "kill it now" — the attribute does not exist on Windows, and the
#: line that uses it never runs there, but a platform-stubbed test would still
#: touch it. The number is stable across POSIX systems.
_SIGKILL = getattr(signal, "SIGKILL", 9)

#: A background job's state — the vocabulary of ``ToolResult.details["status"]``
#: and of the completion notification.
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_KILLED = "killed"
JOB_TIMED_OUT = "timed_out"

#: How long the completion notifier waits before speaking, so several jobs
#: that finish together become one announcement instead of a burst.
_NOTIFY_WINDOW = 0.3

#: How long a killed background job's pipes get to deliver their tail. The
#: process is gone by then; on Windows its own children may survive holding
#: the write ends (there are no process groups to take down with it), so the
#: drain is bounded rather than endless. POSIX never pays this: the group
#: kill closes every writer at once.
_DRAIN_GRACE = 1.0


def _is_wsl_path(path: Path) -> bool:
    normalized = str(path).lower().replace("\\", "/")
    return "system32" in normalized or "windowsapps" in normalized


def find_bash() -> Path | None:
    """Locate a bash executable, cross-platform."""
    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
            Path(r"C:\Git\bin\bash.exe"),
            Path.home() / "AppData" / "Local" / "Programs" / "Git" / "bin" / "bash.exe",
            Path.home() / "scoop" / "apps" / "git" / "current" / "bin" / "bash.exe",
            Path(r"C:\msys64\usr\bin\bash.exe"),
            Path(r"C:\cygwin64\bin\bash.exe"),
            Path(r"C:\cygwin\bin\bash.exe"),
        ]
    else:
        candidates = [
            Path("/bin/bash"),
            Path("/usr/bin/bash"),
            Path("/usr/local/bin/bash"),
            Path("/opt/homebrew/bin/bash"),
        ]
    for path in candidates:
        if path.exists():
            return path
    bash = shutil.which("bash")
    if bash and (sys.platform != "win32" or not _is_wsl_path(Path(bash))):
        return Path(bash)
    sh = shutil.which("sh")
    return Path(sh) if sh else None


def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the command and everything it spawned.

    POSIX: the command was started with ``start_new_session=True``, so it is a
    process-group leader and ``killpg`` takes down the whole tree — the reason
    the group exists. If the group is already gone the direct kill is the
    fallback. Windows has no process groups: only the direct child dies, and
    whatever it spawned is left for the user to clean up.
    """
    if proc.returncode is not None:
        return
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, _SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass  # the group is gone or not ours — fall through to the child
    try:
        proc.kill()
    except ProcessLookupError:
        pass


@dataclass
class _Job:
    """One background command: its process, its bounded output, its state."""

    id: str
    command: str
    proc: asyncio.subprocess.Process
    #: Unread output, per stream. Reading is consuming: what a ``bash_output``
    #: call returned never comes back.
    buf_out: _Ring = field(default_factory=_Ring)
    buf_err: _Ring = field(default_factory=_Ring)
    #: Lines handed to a reader so far — the consumption cursor, for the record.
    consumed: int = 0
    #: Ring overflow already mentioned to a reader, so each reader hears it once.
    announced_discards: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)
    exit_code: int | None = None
    status: str = JOB_RUNNING
    #: Drains the pipes and finishes the job's bookkeeping when the process ends.
    collector: asyncio.Task | None = None
    #: Enforces the background deadline, if the job has one.
    watchdog: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        """Whether the job's process is still alive — its ``done`` not yet set.

        Distinct from having unread output: a finished job keeps its ring until
        something reads it, and that is exactly the case this answers False for.
        """
        return not self.done.is_set()


def _start_order(job: _Job) -> int:
    """A job's position in the start sequence — its id's numeric suffix.

    Ids are minted as ``shell_<n>`` from one monotonically increasing counter,
    so the number *is* the start order. Sorted as strings they would misorder
    at ten (``shell_10`` before ``shell_2``); sorted as this key they cannot.
    """
    return int(job.id.rsplit("_", 1)[1])


class BashSession:
    """Persistent bash session — cwd and env vars survive across commands.

    The session starts in the conversation's project directory and ``restart``
    puts it back there. It deliberately does not use the process working
    directory: several conversations share one process, and a shell that leaked
    its ``cd`` into the next project would be a bug, not a convenience.

    Each command — foreground or background — snapshots the cwd and env at the
    moment it starts; a ``cd`` or ``export`` issued while a background job runs
    does not reach it.

    *host*, when given, is the conversation's context (it grows into a
    :class:`~mocode.host.plugin.context.HostContext` at assembly): background
    jobs report their early output to the turn that started them and announce
    their completion through it. A bare session (tests, direct use) simply has
    nobody to tell.
    """

    def __init__(self, cwd: Path, *, host: BuildContext | None = None):
        self.bash_path: Path | None = None  # resolved lazily on first use
        self._home = Path(cwd).resolve()
        self._cwd = self._home
        self._env_vars: dict[str, str] = {}
        self._host = host
        #: Background jobs of this conversation, by id, in start order.
        self.jobs: dict[str, _Job] = {}
        self._job_seq = 0
        #: Jobs that ended and wait for the idle-time announcement.
        self._pending_done: list[_Job] = []
        self._notifier: asyncio.Task | None = None
        self._notify_seq = 0
        #: How many background jobs may run at once (``plugins.shell.max_background``).
        self.max_background = 16
        #: Hard ceiling on a background job's runtime in seconds
        #: (``plugins.shell.background_timeout``); 0 disables it. A job's own
        #: ``timeout`` argument may lower it, never raise it.
        self.background_timeout = 3600

    def _ensure_bash(self) -> Path:
        if self.bash_path is None:
            self.bash_path = find_bash()
            if not self.bash_path:
                raise RuntimeError(
                    "Bash not found. Please install bash (Git for Windows, MSYS2, or a Unix shell)."
                )
        return self.bash_path

    @property
    def cwd(self) -> str:
        """Where the next command runs — ``cd`` and ``export`` change it.

        The session's working directory, not the process's: ``cd`` is handled
        here rather than in a child, which is the whole reason a shell session
        is persistent.
        """
        return str(self._cwd)

    def configure(self, settings: dict) -> None:
        """Apply the ``plugins.shell`` settings; unknown or bad values stay put.

        ``max_background`` — how many jobs may run at once;
        ``background_timeout`` — the hard ceiling on a job's runtime, seconds
        (0 disables it). Both have safe defaults; a bad value is ignored
        rather than guessed at.
        """
        if not isinstance(settings, dict):
            return
        for key, attr, floor in (
            ("max_background", "max_background", 1),
            ("background_timeout", "background_timeout", 0),
        ):
            try:
                value = int(settings[key])
            except (KeyError, TypeError, ValueError):
                continue
            if value >= floor:  # below the floor is a bad value, not a clamp
                setattr(self, attr, value)

    # ── Foreground ─────────────────────────────────────────

    async def execute(
        self,
        command: str,
        timeout: int,
        on_output: OutputSink | None = None,
    ) -> ToolResult:
        """Run *command* in the foreground and wait for it to finish.

        ``cd`` and ``export`` never reach a child process — they are answered
        here so they change this session. Output is streamed to *on_output* as
        it arrives, which is how a tool reports progress mid-command. ``timeout``
        bounds the call; the killed process reports the timeout as its exit
        code rather than hanging the turn.
        """
        stripped = command.strip()
        if stripped.startswith("cd ") and "&&" not in stripped and ";" not in stripped:
            return ToolResult(self._handle_cd(stripped[3:].strip()), {"exit_code": 0})
        if stripped.startswith("export "):
            return ToolResult(self._handle_export(stripped[7:]), {"exit_code": 0})

        env = self._env_snapshot()
        try:
            proc = await self._spawn(command, env)
        except Exception as e:
            return ToolResult(f"error: {e}")

        out: list[str] = []
        err: list[str] = []
        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout", out.append, on_output)),
            asyncio.create_task(_pump(proc.stderr, "stderr", err.append, on_output)),
        ]
        waiter = asyncio.create_task(proc.wait())
        try:
            await asyncio.wait(
                {waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if waiter.done():
                # The process is gone; the pipes still hold whatever it wrote last.
                await asyncio.gather(*pumps)
            else:
                return ToolResult(f"(timed out after {timeout}s)")
        finally:
            waiter.cancel()
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if proc.returncode is None:
                _terminate(proc)
                await proc.wait()

        output = "".join(out).strip()
        stderr = "".join(err).strip()
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        # The exit code is a fact about the run, not part of what the model
        # needs to read — so it travels as a detail.
        return ToolResult(output or "(empty)", {"exit_code": proc.returncode})

    # ── Background ─────────────────────────────────────────

    async def start_background(
        self,
        command: str,
        *,
        timeout: int | None = None,
        on_output: OutputSink | None = None,
    ) -> ToolResult:
        """Start *command* as a background job and return its handle at once.

        ``timeout`` (the model's explicit argument, not the tool policy's
        deadline) bounds the job itself; without one the configured hard
        ceiling applies, and with it the lower of the two.
        """
        running = sum(1 for job in self.jobs.values() if job.running)
        if running >= self.max_background:
            raise ToolError(
                f"{running} background shells already running "
                f"(limit {self.max_background}); read or kill one first",
                code="limit",
            )

        env = self._env_snapshot()
        try:
            proc = await self._spawn(command, env)
        except Exception as e:
            return ToolResult(f"error: {e}")

        self._job_seq += 1
        job = _Job(id=f"shell_{self._job_seq}", command=command, proc=proc)
        job.collector = asyncio.create_task(self._collect(job, on_output))
        deadline = self._deadline(timeout)
        if deadline > 0:
            job.watchdog = asyncio.create_task(self._watch(job, deadline))
        self.jobs[job.id] = job
        return ToolResult(
            f"started {job.id} (running in background)",
            {"shell_id": job.id, "command": command, "status": job.status},
        )

    async def read_output(
        self,
        shell_id: str,
        *,
        filter: str | None = None,
        wait: bool = False,
        timeout: float | None = None,
    ) -> ToolResult:
        """The new output of a background job — reading is consuming.

        Without *filter*, everything buffered since the last read comes back
        and the buffer empties. With one, only the matching lines come back
        and only they are consumed; the rest stay buffered for a later read.
        ``wait`` blocks until the job completes — one ``asyncio.Event`` await,
        bounded by *timeout* — so "wait for it to finish" is not a poll loop.
        """
        job = self._job(shell_id)
        if wait and job.running:
            try:
                await asyncio.wait_for(job.done.wait(), timeout)
            except asyncio.TimeoutError:
                pass  # the wait bounded itself; report what is there now

        if filter:
            try:
                pattern = re.compile(filter)
            except re.error as e:
                raise ToolError(f"invalid filter regex: {e}", code="invalid_param")
            lines = job.buf_out.take_matching(pattern) + job.buf_err.take_matching(
                pattern
            )
        else:
            lines = job.buf_out.drain() + job.buf_err.drain()
        job.consumed += len(lines)

        discards = job.buf_out.discarded + job.buf_err.discarded
        fresh_discards = discards - job.announced_discards
        job.announced_discards = discards

        parts = []
        if fresh_discards > 0:
            parts.append(f"…({fresh_discards} earlier lines discarded)")
        if lines:
            parts.append("".join(lines).rstrip("\n"))
        content = "\n".join(parts) if parts else "(no new output)"
        return ToolResult(
            content,
            {
                "lines": [line.rstrip("\n") for line in lines],
                "status": job.status,
                "exit_code": job.exit_code,
            },
        )

    async def kill(self, shell_id: str) -> ToolResult:
        """Stop a background job — the whole process group, where there is one."""
        job = self._job(shell_id)
        if not job.running:
            return ToolResult(
                f"{job.id} already {job.status}",
                {"shell_id": job.id, "status": job.status, "exit_code": job.exit_code},
            )
        job.status = JOB_KILLED
        _terminate(job.proc)
        try:  # the collector finishes the bookkeeping once the process is gone
            await asyncio.wait_for(job.done.wait(), 5.0)
        except asyncio.TimeoutError:  # pragma: no cover - a killed process ends
            pass
        return ToolResult(
            f"killed {job.id}",
            {
                "shell_id": job.id,
                "command": job.command,
                "status": job.status,
                "exit_code": job.exit_code,
            },
        )

    def shutdown(self) -> None:
        """Kill every background job — a restart, or the conversation ending."""
        for job in self.jobs.values():
            self._kill_now(job)
        self.jobs.clear()
        self._pending_done.clear()
        if self._notifier is not None and not self._notifier.done():
            self._notifier.cancel()

    # ── Background plumbing ────────────────────────────────

    def _env_snapshot(self) -> dict[str, str] | None:
        return {**os.environ, **self._env_vars} if self._env_vars else None

    async def _spawn(
        self, command: str, env: dict[str, str] | None
    ) -> asyncio.subprocess.Process:
        """One subprocess per command, in its own process group on POSIX."""
        kwargs = {} if sys.platform == "win32" else {"start_new_session": True}
        return await asyncio.create_subprocess_exec(
            str(self._ensure_bash()),
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=env,
            **kwargs,
        )

    def _deadline(self, timeout: int | None) -> int:
        """A background job's deadline: its own argument, capped by the config."""
        if timeout is not None and timeout > 0:
            if self.background_timeout > 0:
                return min(timeout, self.background_timeout)
            return timeout
        return self.background_timeout

    def _job(self, shell_id: str) -> _Job:
        job = self.jobs.get(shell_id)
        if job is None:
            raise ToolError(f"no background shell '{shell_id}'", code="not_found")
        return job

    def _kill_now(self, job: _Job) -> None:
        """Synchronous kill for shutdown paths — the collector mops up after."""
        if job.running:
            job.status = JOB_KILLED
            _terminate(job.proc)
        if job.watchdog is not None and not job.watchdog.done():
            job.watchdog.cancel()

    async def _collect(self, job: _Job, on_output: OutputSink | None) -> None:
        """Drain a job's pipes into its rings, then finish its bookkeeping."""
        pumps = [
            asyncio.create_task(_pump(job.proc.stdout, "stdout", job.buf_out.append, on_output)),
            asyncio.create_task(_pump(job.proc.stderr, "stderr", job.buf_err.append, on_output)),
        ]
        await self._adopt(job, pumps)

    async def _adopt(self, job: _Job, pumps: list[asyncio.Task]) -> None:
        """Wait a job's process out, then finish its bookkeeping.

        A started job's collector creates its own pumps; they have been
        reading these pipes all along, and the rings pick the stream up
        mid-flight.
        """
        try:
            await job.proc.wait()
        finally:
            await self._settle(job, pumps)

    async def _settle(self, job: _Job, pumps: list[asyncio.Task]) -> None:
        """Bound the pipe tail, then close the job's bookkeeping.

        The process is gone; the pipes still hold whatever it wrote last —
        bounded by the grace, because a killed child's own children may
        still hold the write ends (see _DRAIN_GRACE).
        """
        try:
            await asyncio.wait_for(asyncio.gather(*pumps), _DRAIN_GRACE)
        except asyncio.TimeoutError:
            pass
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            job.exit_code = job.proc.returncode
            if job.status == JOB_RUNNING:
                job.status = JOB_COMPLETED
            job.done.set()
            if job.watchdog is not None and not job.watchdog.done():
                job.watchdog.cancel()
            self._enqueue_done(job)

    # ── Completion notification ────────────────────────────

    def _enqueue_done(self, job: _Job) -> None:
        """A job ended — queue it for the idle-time announcement.

        A killed job is not announced: somebody asked for that, and the asker
        knows. A job that ended on its own — completed, or timed out — is news
        for whoever is watching. Bare sessions (no host) have nobody to tell.
        """
        if self._host is None or job.status == JOB_KILLED:
            return
        self._pending_done.append(job)
        if self._notifier is None or self._notifier.done():
            self._notifier = asyncio.create_task(self._announce_done())

    async def _announce_done(self) -> None:
        """One announcement for everything that finished together.

        The completion side only queues; this coroutine waits out the
        coalescing window so a burst of finishers becomes one entry, then
        holds while a turn is running — the model reads what it started with
        ``bash_output`` itself — and speaks once the conversation is idle.
        The entry lists its jobs in **start order**, not completion order:
        whichever of two same-moment finishers happened to enqueue first is
        a scheduling accident, and a deterministic list (shell_1 before
        shell_2 — and shell_10 after shell_9) is what a frontend can render
        without sorting for itself. Each announcement is its own block
        (``shell-bg-<n>``); the message is a ``PluginMessage``, so it reaches
        whoever is watching and is not replayed on a resumed session.
        """
        while True:
            await asyncio.sleep(_NOTIFY_WINDOW)
            agent = getattr(self._host, "agent", None)
            if agent is not None and agent.busy:
                continue
            emit_message = getattr(self._host, "emit_message", None)
            jobs, self._pending_done = self._pending_done, []
            if emit_message is None or not jobs:
                return
            jobs.sort(key=_start_order)
            self._notify_seq += 1
            await emit_message(
                "shell/background-done",
                {
                    "jobs": [
                        {
                            "id": job.id,
                            "command": job.command,
                            "exit_code": job.exit_code,
                            "status": job.status,
                        }
                        for job in jobs
                    ]
                },
                block_id=f"shell-bg-{self._notify_seq}",
            )
            if not self._pending_done:
                return

    async def _watch(self, job: _Job, deadline: float) -> None:
        """Enforce a background job's deadline: kill the group, mark it timed out."""
        try:
            await asyncio.sleep(deadline)
        except asyncio.CancelledError:
            return
        if job.proc.returncode is None:
            job.status = JOB_TIMED_OUT
            _terminate(job.proc)

    # ── Session state ──────────────────────────────────────

    def _handle_cd(self, path: str) -> str:
        if path.startswith("~"):
            path = str(Path.home()) + path[1:]
        new_path = Path(path) if Path(path).is_absolute() else self._cwd / path
        new_path = new_path.resolve()
        if new_path.exists() and new_path.is_dir():
            self._cwd = new_path
            return str(self._cwd)
        return f"bash: cd: {path}: No such file or directory"

    def _handle_export(self, expr: str) -> str:
        if "=" in expr:
            key, value = expr.split("=", 1)
            self._env_vars[key.strip()] = value.strip().strip("\"'")
        return ""

    def restart(self) -> None:
        """Back to where the conversation's project starts — background jobs die."""
        self.shutdown()
        self._cwd = self._home
        self._env_vars.clear()


async def _pump(
    reader: asyncio.StreamReader,
    stream: str,
    append: Callable[[str], None],
    on_output: OutputSink | None,
) -> None:
    """Collect one of the child's output streams, reporting each line as it lands."""
    while True:
        line = await reader.readline()
        if not line:
            return
        text = decode_bytes(line)
        if not text:
            continue
        append(text)
        if on_output is not None:
            await on_output(text, stream)
