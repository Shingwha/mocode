"""Session management command"""

from .base import Command, CommandContext, command
from .result import CommandResult


@command("/session", "/s", description="Manage conversation sessions")
class SessionCommand(Command):
    """Session management command"""

    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        if arg.startswith("restore "):
            return self._restore_session(ctx, arg[8:].strip())

        # No args - return session list for interactive selection
        sessions = ctx.client.list_sessions()
        if not sessions:
            return CommandResult(message="No saved sessions")

        session_data = []
        for s in sessions:
            preview = ""
            for msg in s.messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        preview = content[:30] + "..." if len(content) > 30 else content
                    break
            session_data.append({
                "id": s.id,
                "updated_at": s.updated_at,
                "message_count": s.message_count,
                "preview": preview,
            })

        return CommandResult(data={"sessions": session_data})

    def _restore_session(self, ctx: CommandContext, session_id: str) -> CommandResult:
        if not session_id:
            return CommandResult(success=False, message="Session ID required")

        if ctx.client.agent.messages:
            ctx.client.save_session()

        session = ctx.client.load_session(session_id)
        if session:
            return CommandResult(
                success=True,
                message=f"Restored session: {session_id}",
                data={"session": session},
            )
        return CommandResult(success=False, message=f"Session not found: {session_id}")
