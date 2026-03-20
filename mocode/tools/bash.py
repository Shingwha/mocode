"""Git Bash 会话管理和工具"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional

from .base import Tool, ToolRegistry


def find_git_bash() -> Optional[Path]:
    """查找 Git Bash 可执行文件"""
    possible_paths = [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
        Path(r"C:\Git\bin\bash.exe"),
        Path.home() / "AppData" / "Local" / "Programs" / "Git" / "bin" / "bash.exe",
    ]

    git_path = shutil.which("git")
    if git_path:
        git_dir = Path(git_path).parent.parent
        possible_paths.insert(0, git_dir / "bin" / "bash.exe")

    for path in possible_paths:
        if path.exists():
            return path

    return None


def _decode_bytes(data: bytes) -> str:
    """解码字节数据"""
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "cp936", "gb2312"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class BashSession:
    """Bash 会话 - 在 Python 层维护状态

    工作目录限制：
    - cd 命令只在独立执行时更新会话工作目录
    - 命令链中的 cd (如 "cd /path && ls") 不会持久化到下一条命令
    - 建议使用独立的 cd 命令切换目录

    示例：
        cd /tmp           # 正确：工作目录切换到 /tmp
        cd /tmp && ls     # 限制：ls 在 /tmp 执行，但下条命令仍用原目录
    """

    def __init__(self):
        self.bash_path = find_git_bash()
        if not self.bash_path:
            raise RuntimeError("Git Bash not found. Please install Git for Windows.")

        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars: dict[str, str] = {}

    @property
    def cwd(self) -> str:
        return str(self._cwd)

    def execute(self, command: str, timeout: int = 30) -> str:
        """执行命令"""
        # 处理 cd 命令（在 Python 层维护状态）
        stripped = command.strip()
        if stripped.startswith("cd ") and "&&" not in stripped and ";" not in stripped:
            new_dir = stripped[3:].strip()
            return self._handle_cd(new_dir)

        # 处理 export 命令
        if stripped.startswith("export "):
            return self._handle_export(stripped[7:])

        # 构建带环境变量的命令
        full_cmd = command
        if self._env_vars:
            exports = "; ".join([f'export {k}="{v}"' for k, v in self._env_vars.items()])
            full_cmd = f"{exports}; {command}"

        try:
            result = subprocess.run(
                [str(self.bash_path), "-c", full_cmd],
                capture_output=True,
                timeout=timeout,
                cwd=self._cwd,
            )

            output = _decode_bytes(result.stdout)
            if result.stderr:
                stderr = _decode_bytes(result.stderr)
                if output:
                    output += "\n" + stderr
                else:
                    output = stderr

            return output.strip() or "(empty)"

        except subprocess.TimeoutExpired:
            return f"(timed out after {timeout}s)"
        except Exception as e:
            return f"error: {e}"

    def _handle_cd(self, path: str) -> str:
        """处理 cd 命令"""
        # 展开 ~
        if path.startswith("~"):
            path = str(Path.home()) + path[1:]

        new_path = self._cwd / path if not Path(path).is_absolute() else Path(path)
        new_path = new_path.resolve()

        if new_path.exists() and new_path.is_dir():
            self._cwd = new_path
            return f"{self._cwd}"
        else:
            return f"bash: cd: {path}: No such file or directory"

    def _handle_export(self, expr: str) -> str:
        """处理 export 命令"""
        if "=" in expr:
            key, value = expr.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"\'')
            self._env_vars[key] = value
            return ""
        return ""

    def restart(self):
        """重启会话（重置状态）"""
        self._cwd = Path(os.getcwd()).resolve()
        self._env_vars.clear()


# 全局会话
_session: Optional[BashSession] = None


def get_session() -> BashSession:
    """获取或创建会话"""
    global _session
    if _session is None:
        _session = BashSession()
    return _session


def close_session():
    """关闭会话"""
    global _session
    _session = None


# ============ Tool ============


def _bash(args: dict) -> str:
    """执行 shell 命令（使用 Git Bash 持久化会话）"""
    cmd = args.get("command")
    if not cmd:
        return "error: missing required parameter 'command'"

    # 检查是否是重启命令
    if args.get("restart"):
        global _session
        if _session:
            _session.restart()
            return "Bash session restarted"
        return "error: no active session to restart"

    try:
        session = get_session()
        return session.execute(cmd, timeout=args.get("timeout", 30))
    except RuntimeError as e:
        return f"error: {e}"
    except Exception as e:
        return f"error: {e}"


def register_bash_tools():
    """注册 bash 工具"""
    ToolRegistry.register(
        Tool(
            "bash",
            "Run shell command in persistent Git Bash session",
            {
                "command": "string?",
                "restart": "boolean?",
                "timeout": "number?",
            },
            _bash,
        )
    )
