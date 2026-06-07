"""Model/provider switcher — /model."""

from __future__ import annotations

from ..prompts import Choice, select
from . import Command, CommandContext, CommandResult


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


# ── Commands list ─────────────────────────────────────────

commands: list[Command] = [
    Command("/model", "Switch provider and model", handler=_model),
]
