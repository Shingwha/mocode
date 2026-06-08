"""Bash tool — BashTool for persistent shell sessions."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..core.tool import Tool
from .utils import decode_bytes


def _is_wsl_path(path: Path) -> bool:
    normalized = str(path).lower().replace("\\", "/")
    return "system32" in normalized or "windowsapps" in normalized


def find_bash() -> Optional[Path]:
    """Locate a bash executable, cross-platform."""
    if sys.platform == "win32":
        possible_paths = [
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
        possible_paths = [Path("/bin/bash"), Path("/usr/bin/bash"), Path("/usr/local/bin/bash"), Path("/opt/homebrew/bin/bash")]
    for path in possible_paths:
        if path.exists():
            return path
    bash = shutil.which("bash")
    if bash:
        p = Path(bash)
        if sys.platform != "win32" or not _is_wsl_path(p):
            return p
    sh = shutil.which("sh")
    if sh:
        return Path(sh)
    return None


class BashSession:
    """Persistent bash session — maintains cwd and env vars across commands."""
    def __init__(self):
        self.bash_path: Path | None = None  # lazy — resolved on first use
        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars: dict[str, str] = {}

    def _ensure_bash(self) -> Path:
        if self.bash_path is None:
            self.bash_path = find_bash()
            if not self.bash_path:
                raise RuntimeError("Bash not found. Please install bash (Git for Windows, MSYS2, or a Unix shell).")
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
        bash = str(self._ensure_bash())
        env = None
        if self._env_vars:
            env = {**os.environ, **self._env_vars}
        try:
            result = subprocess.run([bash, "-c", command], capture_output=True,
                                    timeout=timeout, cwd=self._cwd, env=env)
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
        new_path = self._cwd / path if not Path(path).is_absolute() else Path(path)
        new_path = new_path.resolve()
        if new_path.exists() and new_path.is_dir():
            self._cwd = new_path
            return f"{self._cwd}"
        return f"bash: cd: {path}: No such file or directory"

    def _handle_export(self, expr: str) -> str:
        if "=" in expr:
            key, value = expr.split("=", 1)
            self._env_vars[key.strip()] = value.strip().strip("\"'")
        return ""

    def restart(self):
        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars.clear()


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


class BashTool(Tool):
    """Run shell commands in a persistent bash session."""
    def __init__(self, timeout: int = 240) -> None:
        self._session = BashSession()
        self._default_timeout = timeout
        super().__init__(name="bash", description=_BASH_DESC, params=_BASH_PARAMS, func=self._execute)

    def _execute(self, args: dict) -> str:
        cmd = args["command"]
        if args.get("restart"):
            self._session.restart()
            return "Bash session restarted"
        return self._session.execute(cmd, timeout=args.get("timeout", self._default_timeout))
