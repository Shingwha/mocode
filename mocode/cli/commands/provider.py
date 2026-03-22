"""Provider switch command"""

from .base import Command, CommandContext, command
from .utils import parse_selection_arg
from ..ui import (
    SelectMenu,
    MenuAction,
    MenuItem,
    is_cancelled,
    is_action,
    error,
    success,
    ask,
    Wizard,
)
from ..ui.colors import RESET, CYAN, GREEN, DIM, RED


@command("/provider", "/p", description="Switch provider")
class ProviderCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not arg:
            provider = self._select_interactive(ctx.client)
            if not provider:
                return True
            old_provider = self._switch_provider(ctx, provider)
            # Enter model selection
            model = self._select_model_interactive(ctx.client)
            if model:
                ctx.client.set_model(model)
            if ctx.layout:
                ctx.layout.add_command_output(
                    f"{CYAN}{old_provider}{RESET} -> {GREEN}{provider}{RESET} | {CYAN}{ctx.client.current_model}{RESET}"
                )
            return True

        provider = parse_selection_arg(
            arg,
            list(ctx.client.providers.keys()),
            error_handler=error,
        )
        if provider is None:
            if arg.isdigit():
                return True
            error(f"Unknown provider: {arg}")
            return True

        if provider not in ctx.client.providers:
            error(f"Unknown provider: {provider}")
            return True

        old_provider = self._switch_provider(ctx, provider)
        if ctx.layout:
            ctx.layout.add_command_output(
                f"{CYAN}{old_provider}{RESET} -> {GREEN}{provider}{RESET} | {CYAN}{ctx.client.current_model}{RESET}"
            )
        return True

    def _select_interactive(self, client) -> str | None:
        """Interactive provider selection."""
        while True:
            choices = []
            for key, pconfig in client.providers.items():
                display = f"{pconfig.name} ({key})"
                choices.append((key, display))
            choices.append(MenuItem.manage())
            choices.append(MenuItem.exit_())

            menu = SelectMenu(
                f"Select provider (current: {client.current_provider})",
                choices,
                client.current_provider,
            )
            result = menu.show()

            if is_cancelled(result):
                return None
            if is_action(result, MenuAction.MANAGE):
                self._manage_providers(client)
                continue
            return result

    def _manage_providers(self, client) -> None:
        """Provider management menu."""
        while True:
            menu = SelectMenu(
                "Manage providers",
                [
                    MenuItem.add("Add new provider"),
                    MenuItem.edit(),
                    MenuItem.delete(),
                    MenuItem.back(),
                ],
            )
            result = menu.show()

            if is_cancelled(result):
                return
            if is_action(result, MenuAction.ADD):
                self._add_provider_interactive(client)
            elif is_action(result, MenuAction.EDIT):
                self._edit_provider_interactive(client)
            elif is_action(result, MenuAction.DELETE):
                self._delete_provider_interactive(client)

    def _add_provider_interactive(self, client) -> None:
        """Interactively add a new provider."""
        wizard = Wizard(title="Add new provider")

        key = wizard.step(
            "Provider key (e.g., 'anthropic', 'deepseek')",
            hint="Internal identifier for the provider",
            required=True,
            validator=lambda k: True if k not in client.providers else f"Provider '{k}' already exists",
        )
        if key is None:
            return

        name = wizard.step("Display name (e.g., 'Anthropic', 'DeepSeek')", default=key)
        if wizard.cancelled:
            return

        base_url = wizard.step(
            "Base URL",
            hint="e.g., 'https://api.anthropic.com/v1'",
            required=True,
        )
        if base_url is None:
            return

        api_key = wizard.step("API Key (optional, press Enter to skip)")
        if wizard.cancelled:
            return

        models_input = wizard.step(
            "Models (comma-separated, e.g., 'claude-3-opus,claude-3-sonnet')",
            hint="Press Enter to skip and add later via /model",
        )
        if wizard.cancelled:
            return
        models = [m.strip() for m in models_input.split(",") if m.strip()] if models_input else []

        try:
            client.add_provider(key, name, base_url, api_key, models)
            success(f"Added provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _edit_provider_interactive(self, client) -> None:
        """Interactively edit a provider."""
        choices = []
        for key, pconfig in client.providers.items():
            display = f"{pconfig.name} ({key})"
            choices.append((key, display))
        choices.append(MenuItem.back())

        menu = SelectMenu("Select provider to edit", choices)
        key = menu.show()

        if is_cancelled(key):
            return

        pconfig = client.providers[key]
        name = pconfig.name
        base_url = pconfig.base_url
        api_key = pconfig.api_key

        while True:
            name_display = f"{name} {DIM}(current){RESET}"
            base_url_display = f"{base_url} {DIM}(current){RESET}"
            api_key_display = f"{'*' * min(len(api_key), 8) if api_key else '(not set)'} {DIM}(current){RESET}"

            menu = SelectMenu(
                f"Edit provider '{key}' - Select field",
                [
                    ("name", f"Display name: {name_display}"),
                    ("base_url", f"Base URL: {base_url_display}"),
                    ("api_key", f"API Key: {api_key_display}"),
                    MenuItem.done(),
                    MenuItem.back(),
                ],
            )
            field = menu.show()

            if is_cancelled(field):
                return
            if is_action(field, MenuAction.DONE):
                break
            if field == "name":
                new_name = ask("Display name", default=name)
                if new_name is not None:
                    name = new_name
            elif field == "base_url":
                new_base_url = ask("Base URL", default=base_url, required=True)
                if new_base_url is not None:
                    base_url = new_base_url
            elif field == "api_key":
                new_api_key = ask("API Key", default=api_key)
                if new_api_key is not None:
                    api_key = new_api_key

        try:
            client.update_provider(key, name=name, base_url=base_url, api_key=api_key)
            success(f"Updated provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _delete_provider_interactive(self, client) -> None:
        """Interactively delete a provider."""
        choices = []
        for key, pconfig in client.providers.items():
            if len(client.providers) <= 1:
                choices.append(MenuItem.disabled(f"{pconfig.name} ({key}) - cannot delete last provider"))
            else:
                display = f"{pconfig.name} ({key})"
                choices.append((key, display))
        choices.append(MenuItem.back())

        menu = SelectMenu("Select provider to delete", choices)
        key = menu.show()

        if is_cancelled(key) or is_action(key, MenuAction.DISABLED):
            return

        pconfig = client.providers[key]

        if self.confirm_delete(f"{pconfig.name} ({key})"):
            try:
                client.remove_provider(key)
                success(f"Deleted provider '{key}'")
            except ValueError as e:
                error(str(e))

    def _switch_provider(self, ctx: CommandContext, provider_key: str):
        """Switch provider."""
        old_provider = ctx.client.current_provider
        ctx.client.set_provider(provider_key)
        return old_provider

    def _select_model_interactive(self, client) -> str | None:
        """Interactive model selection."""
        models = client.models
        current = client.current_model
        provider_key = client.current_provider

        choices = [(m, m) for m in models]
        choices.append(MenuItem.exit_())

        provider_name = client.providers[provider_key].name if provider_key in client.providers else provider_key
        menu = SelectMenu(f"Select model [{provider_name}] (current: {current})", choices, current)
        result = menu.show()

        return None if is_cancelled(result) else result
