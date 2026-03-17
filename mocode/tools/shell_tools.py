"""Shell 工具"""

from .base import Tool, ToolRegistry
from .bash_session import get_session


def _bash(args: dict) -> str:
    """执行 shell 命令（使用 Git Bash 持久化会话）"""
    # 支持 cmd 或 command 作为参数名
    cmd = args.get("cmd") or args.get("command")
    if not cmd:
        return "error: missing required parameter 'cmd'"

    # 检查是否是重启命令
    if args.get("restart"):
        from .bash_session import _session
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


def register_shell_tools():
    """注册 shell 工具"""
    ToolRegistry.register(
        Tool(
            "bash",
            "Run shell command in persistent Git Bash session",
            {
                "cmd": "string?",
                "command": "string?",
                "restart": "boolean?",
                "timeout": "number?",
            },
            _bash,
        )
    )
