"""shell plugin — run commands in a persistent bash session.

The session is asynchronous so output can be reported as it is produced: a
command that takes a minute is watchable rather than a blank wait, and a
timeout can actually kill the child process instead of abandoning a thread.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Awaitable, Callable

from ....core.events import ToolOutput
from ....core.tool import Tool, ToolResult
from ...text import decode_bytes
from ..base import Plugin
from ..context import HostContext

#: Called with each chunk of output as it arrives: ``on_output(text, stream)``.
OutputSink = Callable[[str, str], Awaitable[None]]

_BASH_TAG = frozenset({"shell"})

_BASH_PARAMS = {
    "command": {"type": "string", "description": "The bash command to execute (Unix-style syntax)"},
    "restart": {"type": "boolean", "optional": True, "description": "Reset session state (working directory and environment variables)"},
    "timeout": {"type": "number", "optional": True, "description": "Max execution time in seconds (default: 240)"},
}
_BASH_DESC = (
    "Run a shell command in a persistent bash session (Unix-style, e.g. ls, grep, find). "
    "Working directory and environment variables persist across commands. "
    "Use 'restart' to reset session state (cwd, env vars)."
)


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


class BashSession:
    """Persistent bash session — cwd and env vars survive across commands."""

    def __init__(self):
        self.bash_path: Path | None = None  # resolved lazily on first use
        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars: dict[str, str] = {}

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
        return str(self._cwd)

    async def execute(
        self,
        command: str,
        timeout: int = 240,
        on_output: OutputSink | None = None,
    ) -> ToolResult:
        stripped = command.strip()
        if stripped.startswith("cd ") and "&&" not in stripped and ";" not in stripped:
            return ToolResult(self._handle_cd(stripped[3:].strip()), {"exit_code": 0})
        if stripped.startswith("export "):
            return ToolResult(self._handle_export(stripped[7:]), {"exit_code": 0})

        env = {**os.environ, **self._env_vars} if self._env_vars else None
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self._ensure_bash()),
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                env=env,
            )
        except Exception as e:
            return ToolResult(f"error: {e}")

        out: list[str] = []
        err: list[str] = []
        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout", out, on_output)),
            asyncio.create_task(_pump(proc.stderr, "stderr", err, on_output)),
        ]
        try:
            try:
                await asyncio.wait_for(proc.wait(), timeout)
            except asyncio.TimeoutError:
                return ToolResult(f"(timed out after {timeout}s)")
            # The process is gone; the pipes still hold whatever it wrote last.
            await asyncio.gather(*pumps)
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

        output = "".join(out).strip()
        stderr = "".join(err).strip()
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        # The exit code is a fact about the run, not part of what the model
        # needs to read — so it travels as a detail.
        return ToolResult(output or "(empty)", {"exit_code": proc.returncode})

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
        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars.clear()


def _tool_output_sink(ctx) -> OutputSink:
    """Publish each line to the run's event stream under this call's id."""

    async def sink(text: str, stream: str) -> None:
        await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text=text, stream=stream))

    return sink


async def _pump(
    reader: asyncio.StreamReader,
    stream: str,
    chunks: list[str],
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
        chunks.append(text)
        if on_output is not None:
            await on_output(text, stream)


class BashTool(Tool):
    """Run shell commands in a persistent bash session."""

    def __init__(self, timeout: int = 240) -> None:
        self._session = BashSession()
        self._default_timeout = timeout
        super().__init__(
            name="bash",
            description=_BASH_DESC,
            params=_BASH_PARAMS,
            func=self._execute,
            tags=_BASH_TAG,
            summary_key="command",
            result_key="exit_code",
        )

    async def _execute(self, args: dict, ctx=None) -> "str | ToolResult":
        if args.get("restart"):
            self._session.restart()
            return ToolResult("Bash session restarted")

        return await self._session.execute(
            args["command"],
            timeout=args.get("timeout", self._default_timeout),
            on_output=_tool_output_sink(ctx) if ctx is not None else None,
        )


class ShellPlugin(Plugin):
    name = "shell"
    description = "Run shell commands in a persistent bash session"

    def build(self, ctx: HostContext) -> None:
        ctx.tools.register(BashTool())


PLUGIN = ShellPlugin()
