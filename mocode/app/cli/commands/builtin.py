"""Built-in commands — /quit, /help, /clear, /copy, /export, /compact, /model, /resume, /connect."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ...config import ModelEntry, ProviderEntry
from ....tools.compact import compact_messages
from ..prompts import Choice, confirm, select, text_input
from ...session import SessionManager
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


# ── /export ───────────────────────────────────────────────


async def _export(ctx: CommandContext) -> CommandResult:
    session = ctx.app.session_mgr.get_active()
    if session is None or not session.messages:
        ctx.display.warn("No active session to export.")
        return CommandResult.CONTINUE

    fmt = ctx.args.strip().lower() or "json"
    system_prompt = ctx.app.agent.system_prompt
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "md":
        path = Path.cwd() / f"session_{ts}.md"
        ctx.app.session_mgr.export_to_md(
            session, path, system_prompt=system_prompt
        )
    else:
        path = Path.cwd() / f"session_{ts}.json"
        ctx.app.session_mgr.export_to_file(
            session, path, system_prompt=system_prompt
        )

    ctx.display.info(f"Exported {len(session.messages)} msgs → {path}")
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


# ── /model ────────────────────────────────────────────────


async def _model(ctx: CommandContext) -> CommandResult:
    # Build provider choices
    provider_choices = []
    for key, entry in ctx.app.config.providers.items():
        title = entry.name or key
        preview = ", ".join(entry.model_names()) or ctx.app.config.active_model
        provider_choices.append(Choice(title=title, value=key, description=preview))

    if not provider_choices:
        ctx.display.warn("No providers configured.")
        return CommandResult.CONTINUE

    chosen_key = await select(
        "Select a provider:",
        provider_choices,
        default=ctx.app.config.active_provider,
    )
    if chosen_key is None:
        return CommandResult.CONTINUE

    entry = ctx.app.config.providers[chosen_key]
    models = entry.model_names()
    # Skip model picker if there's only one (or zero) models
    if len(models) <= 1:
        ctx.app.switch_provider(
            chosen_key, models[0] if models else ctx.app.config.active_model
        )
        return CommandResult.CONTINUE

    model_choices = [
        Choice(
            title=m,
            value=m,
            description="current" if m == ctx.app.config.active_model else None,
        )
        for m in models
    ]
    chosen_model = await select(
        f"Select a model for {entry.name or chosen_key}:",
        model_choices,
        default=(
            ctx.app.config.active_model
            if ctx.app.config.active_model in models
            else models[0]
        ),
    )
    if chosen_model is None:
        return CommandResult.CONTINUE

    ctx.app.switch_provider(chosen_key, chosen_model)
    return CommandResult.CONTINUE


# ── /resume ───────────────────────────────────────────────

MAX_RESUME_CHOICES = 20


async def _resume_from_file(ctx: CommandContext, arg: str):
    """Resume from a portable JSON file."""
    path = Path(arg.strip('"').strip("'")).expanduser()
    result = SessionManager.import_from_file(path)
    if result is None:
        ctx.display.warn(f"Invalid or missing session file: {arg}")
        return
    messages, _title = result
    ctx.app.resume_from_file(messages)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    ctx.display.info(
        f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}"
    )


async def _resume_interactive(ctx: CommandContext):
    """Interactive picker over recent sessions."""
    sessions = ctx.app.session_mgr.list()
    if not sessions:
        ctx.display.info("No sessions found.")
        return

    active_id = ctx.app.session_mgr.active_id
    candidates = [s for s in sessions if s.id != active_id][:MAX_RESUME_CHOICES]
    truncated = len([s for s in sessions if s.id != active_id]) > MAX_RESUME_CHOICES

    if not candidates:
        ctx.display.info("No other sessions to resume.")
        return

    choices = [
        Choice(
            title=(s.title or "Untitled")[:60],
            value=s.id,
            description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
        )
        for s in candidates
    ]

    if truncated:
        choices.append(
            Choice(
                title="(older sessions omitted — use /resume <file.json> to load one)",
                value="__truncated__",
                disabled=True,
            )
        )

    chosen = await select("Resume a session:", choices)
    if chosen is None or chosen == "__truncated__":
        return

    session = ctx.app.session_mgr.resume(chosen)
    if session is None:
        ctx.display.error(f"Session not found: {chosen}")
        return

    ctx.app.resume_session(session)
    user_count = sum(1 for m in session.messages if m.get("role") == "user")
    ctx.display.info(
        f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
    )


async def _resume(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        await _resume_from_file(ctx, ctx.args)
    else:
        await _resume_interactive(ctx)
    return CommandResult.CONTINUE


# ── /connect ──────────────────────────────────────────────


def _mask_key(k: str) -> str:
    """Mask an API key, showing only the last 4 characters."""
    if len(k) >= 4:
        return "•" * (len(k) - 4) + k[-4:]
    return "••••"


async def _connect_extra_body(ctx: CommandContext, entry: ProviderEntry):
    """Sub-menu: pick a model, then edit its extra_body JSON."""
    if not entry.models:
        ctx.display.warn("No models configured for this provider.")
        return

    model_choices = [Choice(title=m.name, value=m.name) for m in entry.models]
    model_choices.append(Choice(title="Back", value="__back__"))
    chosen_model = await select("Select model to edit extra_body:", model_choices)
    if chosen_model is None or chosen_model == "__back__":
        return

    model_entry = None
    for m in entry.models:
        if m.name == chosen_model:
            model_entry = m
            break
    if model_entry is None:
        return

    default_str = (
        json.dumps(model_entry.extra_body, ensure_ascii=False)
        if model_entry.extra_body
        else ""
    )

    result = await text_input(
        f"extra_body for {chosen_model} (JSON or blank to clear):",
        default=default_str,
    )
    if result is None:
        return

    result = result.strip()
    if not result:
        model_entry.extra_body = None
        return

    try:
        parsed = json.loads(result)
    except json.JSONDecodeError as e:
        ctx.display.error(f"Invalid JSON: {e}")
        return

    model_entry.extra_body = parsed


async def _connect_edit(ctx: CommandContext, key: str):
    """Edit submenu for one provider — loop until Back."""
    entry = ctx.app.config.providers.get(key)
    if entry is None:
        return

    dirty = False

    while True:
        name_display = entry.name or key
        models_display = ", ".join(entry.model_names())
        key_masked = _mask_key(entry.api_key)

        choices = [
            Choice(title=f"Edit display name:  {name_display}", value="name"),
            Choice(title=f"Edit API key:       {key_masked}", value="apikey"),
            Choice(
                title=f"Edit base URL:      {entry.base_url or ''}", value="baseurl"
            ),
            Choice(title=f"Edit models:        {models_display}", value="models"),
            Choice(title="Edit per-model extra_body", value="extra_body"),
            Choice(title="Delete provider", value="delete"),
            Choice(title="Back", value="back"),
        ]

        chosen = await select(f"Provider [{key}]:", choices)
        if chosen is None or chosen == "back":
            if dirty:
                ctx.app.config.save()
                if key == ctx.app.config.active_provider:
                    ctx.app.switch_provider(key, ctx.app.config.active_model)
                ctx.display.info("Config saved.")
            return

        if chosen == "name":
            result = await text_input("Display name:", default=entry.name)
            if result is not None:
                entry.name = result
                dirty = True

        elif chosen == "apikey":
            result = await text_input("API key:", default=entry.api_key)
            if result is not None:
                entry.api_key = result
                dirty = True

        elif chosen == "baseurl":
            result = await text_input("Base URL:", default=entry.base_url or "")
            if result is not None:
                entry.base_url = result or None
                dirty = True

        elif chosen == "models":
            default_str = ", ".join(entry.model_names())
            result = await text_input(
                "Models (comma-separated):", default=default_str
            )
            if result is not None:
                new_names = [m.strip() for m in result.split(",") if m.strip()]
                if new_names:
                    old_extra = {m.name: m.extra_body for m in entry.models}
                    entry.models = [
                        ModelEntry(name=n, extra_body=old_extra.get(n))
                        for n in new_names
                    ]
                    if (
                        ctx.app.config.active_model not in new_names
                        and key == ctx.app.config.active_provider
                    ):
                        ctx.app.config.active_model = new_names[0]
                        ctx.display.warn(
                            f"Active model removed, reset to '{new_names[0]}'"
                        )
                    dirty = True
                else:
                    ctx.display.warn("Models list cannot be empty.")

        elif chosen == "extra_body":
            await _connect_extra_body(ctx, entry)
            dirty = True

        elif chosen == "delete":
            if key == ctx.app.config.active_provider:
                ctx.display.warn(
                    f"Cannot delete active provider '{key}'. "
                    "Use /model to switch first."
                )
                continue
            if await confirm(f"Delete provider '{key}'?"):
                del ctx.app.config.providers[key]
                ctx.app.config.save()
                ctx.display.info(f"Provider '{key}' deleted.")
                return


async def _connect_add(ctx: CommandContext):
    """Add a new provider — sequential prompts, abort on any cancel."""
    config = ctx.app.config

    def _validate_key(s: str) -> bool | str:
        if not s.strip():
            return "Key cannot be empty"
        if s.strip() in config.providers:
            return f"Provider '{s.strip()}' already exists"
        return True

    key = await text_input("Provider key (e.g. 'openai'):", validate=_validate_key)
    if key is None:
        return
    key = key.strip()

    name = await text_input("Display name (optional):")
    if name is None:
        return

    base_url = await text_input("Base URL (optional):")
    if base_url is None:
        return

    api_key = await text_input("API key:")
    if api_key is None:
        return
    if not api_key.strip():
        ctx.display.warn("API key cannot be empty. Aborted.")
        return

    def _validate_models(s: str) -> bool | str:
        if not [m.strip() for m in s.split(",") if m.strip()]:
            return "At least one model required"
        return True

    models_str = await text_input(
        "Models (comma-separated):", validate=_validate_models
    )
    if models_str is None:
        return
    model_names = [m.strip() for m in models_str.split(",") if m.strip()]

    config.providers[key] = ProviderEntry(
        name=name.strip() if name else "",
        api_key=api_key.strip(),
        base_url=base_url.strip() or None,
        models=[ModelEntry(name=m) for m in model_names],
    )
    ctx.app.config.save()
    ctx.display.info(f"Provider '{key}' added.")


async def _connect(ctx: CommandContext) -> CommandResult:
    # Top-level menu — pick a provider to edit, or add new
    choices = []
    for key, entry in ctx.app.config.providers.items():
        title = entry.name or key
        preview = ", ".join(entry.model_names()) or ctx.app.config.active_model
        choices.append(Choice(title=title, value=key, description=preview))

    choices.append(Choice(title="+ Add new provider", value="__add__"))
    choices.append(Choice(title="Back", value="__back__"))

    chosen = await select(
        "Manage providers:",
        choices,
        default=ctx.app.config.active_provider,
    )
    if chosen is None or chosen == "__back__":
        return CommandResult.CONTINUE
    if chosen == "__add__":
        await _connect_add(ctx)
        return CommandResult.CONTINUE
    await _connect_edit(ctx, chosen)
    return CommandResult.CONTINUE


# ── Command definitions ───────────────────────────────────


commands: list[Command] = [
    Command("/quit", "Exit the application", aliases=("/exit", "quit", "exit"), handler=_quit),
    Command("/help", "Show available commands", handler=_help),
    Command("/clear", "Clear the current conversation", handler=_clear),
    Command("/copy", "Copy the last assistant response to clipboard", handler=_copy),
    Command("/export", "Export conversation to a file (json|md)", handler=_export),
    Command("/compact", "Compress conversation history to free up context window",
            aliases=("compact",), handler=_compact),
    Command("/model", "Switch provider and model", handler=_model),
    Command("/resume", "Browse and resume sessions", handler=_resume),
    Command("/connect", "Manage providers (add, edit, delete)", handler=_connect),
]
