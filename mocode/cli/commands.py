"""The terminal's own commands — the ones that need a terminal.

The split is by capability, not by history (see :class:`CommandContext`): a
command that needs an interactive picker or the clipboard belongs to the
frontend that offers it, so these stay here. What a conversation can do for
itself — ``/export``, ``/clear``, ``/help``, and the default prompt sections —
arrives from the host's built-in plugins (``session``, ``help``,
``default-prompts``) and reaches every frontend the same way.

They are registered by :class:`~mocode.cli.plugin.BuiltinCommands` — the first
implementation of the terminal's plugin interface, so that contributing to the
terminal has exactly one shape whether you are MoCode or a third party.
"""

from __future__ import annotations

from pathlib import Path

from ..host.command import CONTINUE, EXIT, Command, CommandContext, CommandResult
from ..host.session import load_session_file
from . import dialogs


# ── /quit, /copy ─────────────────────────────────────


async def _quit(_ctx: CommandContext) -> CommandResult:
    return EXIT


async def _copy(ctx: CommandContext) -> CommandResult:
    """Copy the last plain assistant response to the clipboard."""
    import pyperclip

    conversation = ctx.conversation
    for msg in reversed(conversation.messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not content or msg.get("tool_calls"):
            continue
        try:
            pyperclip.copy(content)
            preview = content[:60].replace("\n", " ").strip()
            suffix = "…" if len(content) > 60 else ""
            await conversation.notify(f"Copied: {preview}{suffix}")
        except Exception as e:
            await conversation.notify(f"Clipboard error: {e}", level="error")
        return CONTINUE

    await conversation.notify("No assistant response to copy.", level="warn")
    return CONTINUE


# ── /model ───────────────────────────────────────────


async def _model(ctx: CommandContext) -> CommandResult:
    """Two decisions, deliberately kept apart: which model *this conversation*
    runs on, and which model new conversations start from. The terminal applies
    both, because that is what a user typing ``/model`` means; the host keeps
    them separate so an application can do only the first."""
    conversation = ctx.conversation
    config = conversation.runtime.config

    provider_choices = [
        dialogs.Choice(
            title=entry.label(key),
            value=key,
            description=", ".join(entry.model_names()) or "(no models defined)",
        )
        for key, entry in config.providers.items()
    ]
    if not provider_choices:
        await conversation.notify(f"No providers defined in {config.path}.", level="warn")
        return CONTINUE

    chosen_key = await dialogs.select(
        "Select a provider:", provider_choices, default=conversation.provider_key
    )
    if chosen_key is None:
        return CONTINUE

    entry = config.providers[chosen_key]
    models = entry.model_names()
    if not models:
        await conversation.notify(
            f"Provider '{chosen_key}' has no models defined in {config.path}.",
            level="warn",
        )
        return CONTINUE

    chosen_model = models[0]
    if len(models) > 1:
        picked = await dialogs.select(
            f"Select a model for {entry.label(chosen_key)}:",
            [
                dialogs.Choice(
                    title=name,
                    value=name,
                    description="current" if name == conversation.model_name else None,
                )
                for name in models
            ],
            default=(
                conversation.model_name
                if conversation.model_name in models
                else models[0]
            ),
        )
        if picked is None:
            return CONTINUE
        chosen_model = picked

    conversation.set_model(chosen_key, chosen_model)
    # …and remember it as the default, which is the only thing here that writes
    # config.json. A terminal user switching models means "use this from now on".
    conversation.runtime.set_default_model(chosen_key, chosen_model)
    await conversation.notify(
        f"Switched to {entry.label(chosen_key)} / {chosen_model}"
    )
    return CONTINUE


# ── /resume ──────────────────────────────────────────

MAX_RESUME_CHOICES = 20


async def _resume_from_file(ctx: CommandContext, arg: str) -> None:
    path = Path(arg.strip('"').strip("'")).expanduser()
    result = load_session_file(path)
    if result is None:
        await ctx.conversation.notify(
            f"Invalid or missing session file: {arg}", level="warn"
        )
        return

    messages, _title = result
    await ctx.conversation.new_session(messages)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    await ctx.conversation.notify(
        f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
    )


async def _resume_interactive(ctx: CommandContext) -> None:
    """Interactive picker over recent sessions."""
    conversation = ctx.conversation
    sessions = conversation.list_sessions()
    if not sessions:
        await conversation.notify("No sessions found.")
        return

    others = [s for s in sessions if s.id != conversation.id]
    candidates = others[:MAX_RESUME_CHOICES]

    if not candidates:
        await conversation.notify("No other sessions to resume.")
        return

    choices = [
        dialogs.Choice(
            title=(s.title or "Untitled")[:60],
            value=s.id,
            description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
        )
        for s in candidates
    ]
    if len(others) > MAX_RESUME_CHOICES:
        choices.append(
            dialogs.Choice(
                title="(older sessions omitted — use /resume <file.json> to load one)",
                value="__truncated__",
                disabled=True,
            )
        )

    chosen = await dialogs.select("Resume a session:", choices)
    if chosen is None or chosen == "__truncated__":
        return

    session = next((s for s in candidates if s.id == chosen), None)
    if session is None:
        await conversation.notify(f"Session not found: {chosen}", level="error")
        return

    await conversation.load_session(session)
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    await conversation.notify(
        f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
    )


async def _resume(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        await _resume_from_file(ctx, ctx.args)
    else:
        await _resume_interactive(ctx)
    return CONTINUE


#: Every command the terminal ships, in the order they are registered.
COMMANDS = (
    Command("/quit", "Exit the application", handler=_quit, aliases=("/exit", "quit", "exit")),
    Command("/copy", "Copy the last assistant response to clipboard", handler=_copy),
    Command("/model", "Switch provider and model", handler=_model),
    Command("/resume", "Browse and resume sessions", handler=_resume),
)
