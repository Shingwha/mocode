"""Miscellaneous commands — /quit, /help, /clear, /copy."""

from __future__ import annotations

from . import CONTINUE, EXIT, Command, CommandContext, CommandResult


async def _quit(ctx: CommandContext) -> CommandResult:
    return EXIT


async def _help(ctx: CommandContext) -> CommandResult:
    """List every registered command — including plugin-contributed ones."""
    commands = ctx.app.commands.all()
    if not commands or ctx.display is None:
        return CONTINUE

    width = max(len(c.name) for c in commands)
    lines = []
    for cmd in commands:
        readable = ", ".join(a for a in cmd.aliases if not a.startswith("/"))
        alias = f"  (also: {readable})" if readable else ""
        lines.append(f"  {cmd.name:<{width}}  {cmd.description}{alias}")
    ctx.display.info("Commands:\n" + "\n".join(lines))
    return CONTINUE


async def _clear(ctx: CommandContext) -> CommandResult:
    ctx.app.clear_conversation()
    if ctx.display:
        ctx.display.info("Session saved and cleared.")
    return CONTINUE


async def _copy(ctx: CommandContext) -> CommandResult:
    """Copy the last plain assistant response to the clipboard."""
    display = ctx.display
    if display is None:
        return CONTINUE

    for msg in reversed(ctx.app.agent.messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not content or msg.get("tool_calls"):
            continue
        try:
            import pyperclip

            pyperclip.copy(content)
            preview = content[:60].replace("\n", " ").strip()
            suffix = "…" if len(content) > 60 else ""
            display.info(f"Copied: {preview}{suffix}")
        except Exception as e:
            display.error(f"Clipboard error: {e}")
        return CONTINUE

    display.warn("No assistant response to copy.")
    return CONTINUE


commands: list[Command] = [
    Command("/quit", "Exit the application", handler=_quit, aliases=("/exit", "quit", "exit")),
    Command("/help", "Show available commands", handler=_help),
    Command("/clear", "Clear the current conversation", handler=_clear),
    Command("/copy", "Copy the last assistant response to clipboard", handler=_copy),
]
