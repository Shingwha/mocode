"""Provider switch command"""

from .base import Command, CommandContext, command
from .result import CommandResult
from .utils import resolve_selection


@command("/provider", "/p", description="Switch provider")
class ProviderCommand(Command):
    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        if not arg:
            # Return available providers for interactive selection
            providers = {
                key: {"name": p.name, "models": p.models}
                for key, p in ctx.client.providers.items()
            }
            return CommandResult(
                data={
                    "providers": providers,
                    "current_provider": ctx.client.current_provider,
                    "current_model": ctx.client.current_model,
                }
            )

        parts = arg.split()
        provider = resolve_selection(parts[0], list(ctx.client.providers.keys()))
        if provider is None or provider not in ctx.client.providers:
            return CommandResult(success=False, message=f"Unknown provider: {arg}")

        old = ctx.client.current_provider
        ctx.client.set_provider(provider)

        model = parts[1] if len(parts) > 1 else None
        if model:
            ctx.client.set_model(model)

        return CommandResult(
            success=True,
            message=f"{old} -> {provider} | {ctx.client.current_model}",
            data={
                "old_provider": old,
                "new_provider": provider,
                "model": ctx.client.current_model,
            },
        )
