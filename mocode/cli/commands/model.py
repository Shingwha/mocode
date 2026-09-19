"""Model/provider switcher — /model.

Two decisions, deliberately kept apart: which model *this conversation* runs on,
and which model new conversations start from. The terminal applies both, because
that is what a user typing ``/model`` means; the host keeps them separate so an
application can do only the first.
"""

from __future__ import annotations

from .. import dialogs
from ...host.command import CONTINUE, Command, CommandContext, CommandResult


async def _model(ctx: CommandContext) -> CommandResult:
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


commands: list[Command] = [
    Command("/model", "Switch provider and model", handler=_model),
]
