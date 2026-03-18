"""Session 管理命令"""

from .base import Command, CommandContext, command
from ..ui.widgets import SelectMenu
from ..ui.colors import DIM, RESET
from ..ui.components import format_success, format_info, format_error


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

        if arg == "list":
            return self._list_sessions(ctx)
        elif arg.startswith("restore "):
            session_id = arg[8:].strip()
            return self._restore_session(ctx, session_id)
        else:
            # 默认：显示 SelectMenu 选择 session 恢复
            return self._restore_interactive(ctx)

    def _list_sessions(self, ctx: CommandContext) -> bool:
        """列出所有 session"""
        sessions = ctx.client.list_sessions()

        if not sessions:
            if ctx.layout:
                ctx.layout.add_command_output(format_info("No saved sessions"))
            return True

        if ctx.layout:
            ctx.layout.add_command_output(format_info("Saved sessions:"))
            for session in sessions:
                display = self._format_display(session)
                ctx.layout.add_command_output(f"  {DIM}{session.id}{RESET} - {display}")

        return True

    def _restore_interactive(self, ctx: CommandContext) -> bool:
        """交互式选择并恢复 session"""
        sessions = ctx.client.list_sessions()

        if not sessions:
            if ctx.layout:
                ctx.layout.add_command_output(format_info("No saved sessions"))
            return True

        # 构建选项列表
        choices = [(s.id, self._format_display(s)) for s in sessions]

        # 使用 SelectMenu 选择
        menu = SelectMenu("Select session to restore", choices)
        selected = menu.show()

        if selected:
            session = ctx.client.load_session(selected)
            if session:
                if ctx.layout:
                    ctx.layout.add_command_output(
                        format_success(f"Restored session: {selected}")
                    )
                    ctx.layout.add_command_output(
                        format_info(f"Messages: {session.message_count}, Model: {session.model}")
                    )
            else:
                if ctx.layout:
                    ctx.layout.add_command_output(
                        format_error(f"Failed to load session: {selected}")
                    )

        return True

    def _restore_session(self, ctx: CommandContext, session_id: str) -> bool:
        """恢复指定 session"""
        if not session_id:
            if ctx.layout:
                ctx.layout.add_command_output(format_error("Session ID required"))
            return True

        session = ctx.client.load_session(session_id)
        if session:
            if ctx.layout:
                ctx.layout.add_command_output(
                    format_success(f"Restored session: {session_id}")
                )
                ctx.layout.add_command_output(
                    format_info(f"Messages: {session.message_count}, Model: {session.model}")
                )
        else:
            if ctx.layout:
                ctx.layout.add_command_output(
                    format_error(f"Session not found: {session_id}")
                )

        return True

    def _format_display(self, session) -> str:
        """格式化 session 显示文本

        格式: 2026-03-18 14:30 (12 msgs) - gpt-4o
        """
        # 提取日期时间部分 (session_20260318_143022 -> 2026-03-18 14:30)
        try:
            session_id = session.id
            # session_YYYYMMDD_HHMMSS
            date_str = session_id[8:18]  # YYYYMMDD
            time_str = session_id[19:25]  # HHMMSS
            formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}"
        except (IndexError, ValueError):
            formatted = session.created_at[:16] if session.created_at else "unknown"

        return f"{formatted} ({session.message_count} msgs) - {session.model or 'unknown'}"
