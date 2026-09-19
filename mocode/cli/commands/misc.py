"""Miscellaneous terminal commands — /quit, /help, /clear, /copy."""

from __future__ import annotations

from ...host.command import CONTINUE, EXIT, Command, CommandContext, CommandResult


async def _quit(ctx: CommandContext) -> CommandResult:
    return EXIT


async def _help(ctx: CommandContext) -> CommandResult:
    """List every registered command — including plugin-contributed ones."""
    if ctx.commands is None:
        return CONTINUE

    commands = ctx.commands.all()
    if not commands:
        return CONTINUE

    width = max(len(c.name) for c in commands)
    lines = []
    for cmd in commands:
        readable = ", ".join(a for a in cmd.aliases if not a.startswith("/"))
        alias = f"  (also: {readable})" if readable else ""
        lines.append(f"  {cmd.name:<{width}}  {cmd.description}{alias}")
    await ctx.conversation.notify("Commands:\n" + "\n".join(lines))
    return CONTINUE


async def _clear(ctx: CommandContext) -> CommandResult:
    await ctx.conversation.start()
    return CONTINUE


async def _copy(ctx: CommandContext) -> CommandResult:
    """Copy the last plain assistant response to the clipboard."""
    conversation = ctx.conversation
    for msg in reversed(conversation.messages):
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
            await conversation.notify(f"Copied: {preview}{suffix}")
        except Exception as e:
            await conversation.notify(f"Clipboard error: {e}", level="error")
        return CONTINUE

    await conversation.notify("No assistant response to copy.", level="warn")
    return CONTINUE


commands: list[Command] = [
    Command("/quit", "Exit the application", handler=_quit, aliases=("/exit", "quit", "exit")),
    Command("/help", "Show available commands", handler=_help),
    Command("/clear", "Clear the current conversation", handler=_clear),
    Command("/copy", "Copy the last assistant response to clipboard", handler=_copy),
]
