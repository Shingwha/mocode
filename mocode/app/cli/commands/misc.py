"""Miscellaneous commands — /quit, /help, /clear, /copy, /compact."""

from __future__ import annotations

from ....core.compact import compact_messages
from . import Command, CommandContext, CommandResult


# ── /quit ─────────────────────────────────────────────────


async def _quit(ctx: CommandContext) -> CommandResult:
    return CommandResult.EXIT


# ── /help ─────────────────────────────────────────────────


async def _help(ctx: CommandContext) -> CommandResult:
    commands = ctx.app.commands.all()
    if not commands:
        ctx.display.info("No commands available.")
        return CommandResult.CONTINUE

    max_len = max(len(c.name) for c in commands)
    lines = []
    for cmd in commands:
        alias_str = ""
        if cmd.aliases:
            readable = ", ".join(a for a in cmd.aliases if not a.startswith("/"))
            if readable:
                alias_str = f"  (also: {readable})"
        lines.append(f"  {cmd.name:<{max_len}}  {cmd.description}{alias_str}")

    ctx.display.info("Commands:\n" + "\n".join(lines))
    return CommandResult.CONTINUE


# ── /clear ────────────────────────────────────────────────


async def _clear(ctx: CommandContext) -> CommandResult:
    ctx.app.clear_conversation()
    ctx.display.info("Session saved and cleared.")
    return CommandResult.CONTINUE


# ── /copy ─────────────────────────────────────────────────


async def _copy(ctx: CommandContext) -> CommandResult:
    messages = ctx.app.agent.messages

    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            if content and not tool_calls:
                try:
                    import pyperclip

                    pyperclip.copy(content)
                    preview = content[:60].replace("\n", " ").strip()
                    suffix = "…" if len(content) > 60 else ""
                    ctx.display.info(f"Copied: {preview}{suffix}")
                except Exception as e:
                    ctx.display.error(f"Clipboard error: {e}")
                return CommandResult.CONTINUE

    ctx.display.warn("No assistant response to copy.")
    return CommandResult.CONTINUE


# ── /compact ──────────────────────────────────────────────


async def _compact(ctx: CommandContext) -> CommandResult:
    agent = ctx.app.agent
    if not agent.messages:
        ctx.display.info("No messages to compact.")
        return CommandResult.CONTINUE

    old_count = len(agent.messages)
    ctx.display.info("Compacting conversation...")

    new_messages = await compact_messages(agent.provider, agent.messages)
    agent.messages.clear()
    agent.messages.extend(new_messages)

    new_count = len(new_messages)
    ctx.display.info(f"Compacted: {old_count} → {new_count} messages")

    ctx.app._save_current_session()
    return CommandResult.CONTINUE


# ── Commands list ─────────────────────────────────────────

commands: list[Command] = [
    Command("/quit", "Exit the application", aliases=("/exit", "quit", "exit"), handler=_quit),
    Command("/help", "Show available commands", handler=_help),
    Command("/clear", "Clear the current conversation", handler=_clear),
    Command("/copy", "Copy the last assistant response to clipboard", handler=_copy),
    Command("/compact", "Compress conversation history to free up context window",
            aliases=("compact",), handler=_compact),
]
