"""shell plugin — run commands in a persistent bash session."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ....core.tool import Tool
from ...text import decode_bytes
from ..base import Plugin
from ..context import HostContext

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

    def execute(self, command: str, timeout: int = 240) -> str:
        stripped = command.strip()
        if stripped.startswith("cd ") and "&&" not in stripped and ";" not in stripped:
            return self._handle_cd(stripped[3:].strip())
        if stripped.startswith("export "):
            return self._handle_export(stripped[7:])
        env = {**os.environ, **self._env_vars} if self._env_vars else None
        try:
            result = subprocess.run(
                [str(self._ensure_bash()), "-c", command],
                capture_output=True,
                timeout=timeout,
                cwd=self._cwd,
                env=env,
            )
            output = decode_bytes(result.stdout)
            if result.stderr:
                stderr = decode_bytes(result.stderr)
                output = (output + "\n" + stderr) if output else stderr
            return output.strip() or "(empty)"
        except subprocess.TimeoutExpired:
            return f"(timed out after {timeout}s)"
        except Exception as e:
            return f"error: {e}"

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
        )

    def _execute(self, args: dict) -> str:
        if args.get("restart"):
            self._session.restart()
            return "Bash session restarted"
        return self._session.execute(
            args["command"], timeout=args.get("timeout", self._default_timeout)
        )


class ShellPlugin(Plugin):
    name = "shell"
    description = "Run shell commands in a persistent bash session"

    def build(self, ctx: HostContext) -> None:
        ctx.tools.register(BashTool())


PLUGIN = ShellPlugin()
