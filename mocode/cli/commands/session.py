"""Session 管理命令"""

from ..ui.colors import DIM, RESET
from ..ui.components import format_error, format_info, format_success
from ..ui.widgets import SelectMenu
from .base import Command, CommandContext, command


@command("/session", "/s", description="管理对话会话")
class SessionCommand(Command):
    """Session 管理命令

    支持以下用法:
        /session         - 显示 SelectMenu 选择并恢复历史 session
        /session list    - 列出所有 session
        /session restore <id> - 恢复指定 session
    """

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not ctx.layout:
            return True

        if arg == "list":
            return self._list_sessions(ctx)
        if arg.startswith("restore "):
            return self._restore_session(ctx, arg[8:].strip())

        return self._restore_interactive(ctx)

    def _list_sessions(self, ctx: CommandContext) -> bool:
        """列出所有 session"""
        sessions = ctx.client.list_sessions()
        if not sessions:
            ctx.layout.add_command_output(format_info("No saved sessions"))
            return True

        ctx.layout.add_command_output(format_info("Saved sessions:"))
        for session in sessions:
            ctx.layout.add_command_output(
                f"  {DIM}{session.id}{RESET} - {self._format_display(session)}"
            )
        return True

    def _restore_interactive(self, ctx: CommandContext) -> bool:
        """交互式选择并恢复 session"""
        sessions = ctx.client.list_sessions()
        if not sessions:
            ctx.layout.add_command_output(format_info("No saved sessions"))
            return True

        choices = [(s.id, self._format_display(s)) for s in sessions]
        choices.append(("__EXIT__", f"{DIM}← Cancel{RESET}"))

        selected = SelectMenu("Select session to restore", choices).show()
        if selected and selected != "__EXIT__":
            self._load_and_display(ctx, selected)
        return True

    def _restore_session(self, ctx: CommandContext, session_id: str) -> bool:
        """恢复指定 session"""
        if not session_id:
            ctx.layout.add_command_output(format_error("Session ID required"))
            return True
        self._load_and_display(ctx, session_id)
        return True

    def _load_and_display(self, ctx: CommandContext, session_id: str) -> None:
        """加载 session 并显示结果"""
        session = ctx.client.load_session(session_id)
        if session:
            ctx.layout.render_session_history(session.messages)
            ctx.layout.add_command_output(
                format_success(f"Restored session: {session_id}")
            )
        else:
            ctx.layout.add_command_output(
                format_error(f"Session not found: {session_id}")
            )

    def _format_display(self, session) -> str:
        """格式化 session 显示文本

        格式: 03-18 14:30 "这是首个用户消息的预览..."
        """
        # 使用更新时间: 2026-03-18T14:30:22 -> 03-18 14:30
        formatted_time = session.updated_at[5:16].replace("T", " ") if session.updated_at else "unknown"

        # 提取首个 user 消息的预览
        preview = ""
        for msg in session.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    preview = content[:30] + "..." if len(content) > 30 else content
                break

        if preview:
            return f'{formatted_time} "{preview}"'
        return formatted_time
