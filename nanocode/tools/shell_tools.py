"""Shell 工具"""

import subprocess

from .base import Tool, ToolRegistry


def _bash(args: dict) -> str:
    """执行 shell 命令"""
    # 支持 cmd 或 command 作为参数名
    cmd = args.get("cmd") or args.get("command")
    if not cmd:
        return "error: missing required parameter 'cmd'"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        return output.strip() or "(empty)"
    except subprocess.TimeoutExpired:
        return "(timed out after 30s)"
    except Exception as e:
        return f"error: {e}"


def register_shell_tools():
    """注册 shell 工具"""
    ToolRegistry.register(
        Tool(
            "bash",
            "Run shell command",
            {"cmd": "string"},
            _bash,
        )
    )
