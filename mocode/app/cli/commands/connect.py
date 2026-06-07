"""Provider connection management — /connect."""

from __future__ import annotations

import json

from ...config import ModelEntry, ProviderEntry
from ..prompts import Choice, confirm, select, text_input
from . import Command, CommandContext, CommandResult


# ── Helpers ───────────────────────────────────────────────


def _mask_key(k: str) -> str:
    """Mask an API key, showing only the last 4 characters."""
    if len(k) >= 4:
        return "•" * (len(k) - 4) + k[-4:]
    return "••••"


# ── /connect:extra_body ──────────────────────────────────


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


# ── /connect:edit ─────────────────────────────────────────


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


# ── /connect:add ──────────────────────────────────────────


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


# ── /connect (top-level) ─────────────────────────────────


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


# ── Commands list ─────────────────────────────────────────

commands: list[Command] = [
    Command("/connect", "Manage providers (add, edit, delete)", handler=_connect),
]
