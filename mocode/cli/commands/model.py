"""Model/provider switcher — /model."""

from __future__ import annotations

from .. import dialogs
from ...host.command import CONTINUE, Command, CommandContext, CommandResult


async def _model(ctx: CommandContext) -> CommandResult:
    config = ctx.app.config
    frontend = ctx.frontend

    provider_choices = [
        dialogs.Choice(
            title=entry.label(key),
            value=key,
            description=", ".join(entry.model_names()) or "(no models defined)",
        )
        for key, entry in config.providers.items()
    ]
    if not provider_choices:
        if frontend:
            frontend.warn(f"No providers defined in {config.path}.")
        return CONTINUE

    chosen_key = await dialogs.select(
        "Select a provider:", provider_choices, default=config.active_provider
    )
    if chosen_key is None:
        return CONTINUE

    entry = config.providers[chosen_key]
    models = entry.model_names()
    if not models:
        if frontend:
            frontend.warn(f"Provider '{chosen_key}' has no models defined in {config.path}.")
        return CONTINUE

    chosen_model = models[0]
    if len(models) > 1:
        picked = await dialogs.select(
            f"Select a model for {entry.label(chosen_key)}:",
            [
                dialogs.Choice(
                    title=name,
                    value=name,
                    description="current" if name == config.active_model else None,
                )
                for name in models
            ],
            default=config.active_model if config.active_model in models else models[0],
        )
        if picked is None:
            return CONTINUE
        chosen_model = picked

    ctx.app.switch_provider(chosen_key, chosen_model)
    if frontend:
        frontend.info(f"Switched to {entry.label(chosen_key)} / {chosen_model}")
    return CONTINUE


commands: list[Command] = [
    Command("/model", "Switch provider and model", handler=_model),
]
