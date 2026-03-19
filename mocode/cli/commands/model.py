"""Model switch command"""

from .base import Command, CommandContext, command
from ..ui import (
    SelectMenu,
    MenuAction,
    MenuItem,
    is_cancelled,
    is_action,
    error,
    success,
    ask,
    format_success,
)
from ..ui.colors import RESET, DIM


@command("/model", "/m", description="Switch model")
class ModelCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        quiet = arg == "--quiet"

        if not arg or quiet:
            model = self._select_interactive(ctx.client)
            if not model:
                return True
        elif arg.isdigit():
            models = ctx.client.models
            num = int(arg)
            if 1 <= num <= len(models):
                model = models[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            model = arg

        self._switch_model(ctx, model, quiet=quiet)
        return True

    def _select_interactive(self, client) -> str | None:
        """Interactive model selection."""
        while True:
            models = client.models
            current = client.current_model
            provider_key = client.current_provider

            choices = [(m, m) for m in models]
            choices.append(MenuItem.manage())
            choices.append(MenuItem.exit_())

            provider_name = client.providers[provider_key].name if provider_key in client.providers else provider_key
            menu = SelectMenu(f"Select model [{provider_name}] (current: {current})", choices, current)
            result = menu.show()

            if is_cancelled(result):
                return None
            if is_action(result, MenuAction.MANAGE):
                self._manage_models(client)
                continue
            return result

    def _manage_models(self, client) -> None:
        """Model management menu."""
        while True:
            menu = SelectMenu(
                "Manage models",
                [
                    MenuItem.add("Add new model"),
                    MenuItem.delete(),
                    MenuItem.back(),
                ],
            )
            result = menu.show()

            if is_cancelled(result):
                return
            if is_action(result, MenuAction.ADD):
                self._add_model_interactive(client)
            elif is_action(result, MenuAction.DELETE):
                self._delete_model_interactive(client)

    def _add_model_interactive(self, client) -> None:
        """Interactively add a new model."""
        model = ask("Model name (e.g., 'gpt-4o', 'claude-3-opus')", required=True)
        if model is None:
            return

        try:
            client.add_model(model)
            success(f"Added model '{model}'")
        except ValueError as e:
            error(str(e))

    def _delete_model_interactive(self, client) -> None:
        """Interactively delete a model."""
        models = client.models
        provider_key = client.current_provider

        if not models:
            error("No models to delete")
            return

        choices = []
        for m in models:
            if len(models) <= 1:
                choices.append(MenuItem.disabled(f"{m} - cannot delete last model"))
            else:
                choices.append((m, m))
        choices.append(MenuItem.back())

        provider_name = client.providers[provider_key].name if provider_key in client.providers else provider_key
        menu = SelectMenu(f"Select model to delete [{provider_name}]", choices)
        model = menu.show()

        if is_cancelled(model) or is_action(model, MenuAction.DISABLED):
            return

        if self.confirm_delete(model):
            try:
                client.remove_model(model)
                success(f"Deleted model '{model}'")
            except ValueError as e:
                error(str(e))

    def _switch_model(self, ctx: CommandContext, model: str, quiet: bool = False):
        """Execute model switch and save config."""
        ctx.client.set_model(model)
        if not quiet and ctx.layout:
            ctx.layout.add_command_output(format_success(f"Switched to {model}"))
