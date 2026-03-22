"""Session management command"""

from ..ui import SelectMenu, MenuItem, is_cancelled, format_error, format_info, format_success
from .base import Command, CommandContext, command


@command("/session", "/s", description="Manage conversation sessions")
class SessionCommand(Command):
    """Session management command

    Usage:
        /session              - Select and restore session interactively
        /session restore <id> - Restore specific session by ID
    """

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not ctx.layout:
            return True

        if arg.startswith("restore "):
            return self._restore_session(ctx, arg[8:].strip())

        return self._restore_interactive(ctx)

    def _restore_interactive(self, ctx: CommandContext) -> bool:
        """Interactively select and restore session."""
        sessions = ctx.client.list_sessions()
        if not sessions:
            ctx.layout.add_command_output(format_info("No saved sessions"))
            return True

        choices = [(s.id, self._format_display(s)) for s in sessions]
        choices.append(MenuItem.exit_())

        selected = SelectMenu("Select session to restore", choices).show()
        if selected and not is_cancelled(selected):
            self._load_and_display(ctx, selected)
        return True

    def _restore_session(self, ctx: CommandContext, session_id: str) -> bool:
        """Restore specific session."""
        if not session_id:
            ctx.layout.add_command_output(format_error("Session ID required"))
            return True
        self._load_and_display(ctx, session_id)
        return True

    def _load_and_display(self, ctx: CommandContext, session_id: str) -> None:
        """Load session and display result."""
        # Save current session if there are messages
        if ctx.client.agent.messages:
            ctx.client.save_session()

        session = ctx.client.load_session(session_id)
        if session:
            ctx.layout.render_session_history(session.messages)
            ctx.layout.add_command_output(format_success(f"Restored session: {session_id}"))
        else:
            ctx.layout.add_command_output(format_error(f"Session not found: {session_id}"))

    def _format_display(self, session) -> str:
        """Format session display text.

        Format: 03-18 14:30 "First user message preview..."
        """
        formatted_time = session.updated_at[5:16].replace("T", " ") if session.updated_at else "unknown"

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
