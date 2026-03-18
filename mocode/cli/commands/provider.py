"""供应商切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error, success, Wizard, parse_selection_arg
from ..ui.colors import RESET, CYAN, GREEN


@command("/provider", "/p", description="切换供应商")
class ProviderCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not arg:
            # 交互式选择 provider
            provider = self._select_interactive(ctx.client)
            if not provider:
                return True
            # 先切换 provider（不输出）
            old_provider = self._switch_provider(ctx, provider)
            # 再进入 model 选择
            from .model import ModelCommand
            cmd = ModelCommand()
            # 标记为静默模式（通过特殊 args）
            original_args = ctx.args
            ctx.args = "--quiet"
            result = cmd.execute(ctx)
            ctx.args = original_args
            # 最后输出切换信息
            if ctx.layout:
                ctx.layout.add_command_output(f"{CYAN}{old_provider}{RESET} → {GREEN}{provider}{RESET} | {CYAN}{ctx.client.current_model}{RESET}")
            return result

        # 解析参数（支持数字索引或直接指定）
        provider = parse_selection_arg(
            arg,
            list(ctx.client.providers.keys()),
            error_handler=error,
        )
        if provider is None:
            if arg.isdigit():
                return True  # error already printed
            error(f"Unknown provider: {arg}")
            return True

        if provider not in ctx.client.providers:
            error(f"Unknown provider: {provider}")
            return True

        # 切换供应商（直接指定时立即输出）
        old_provider = self._switch_provider(ctx, provider)
        if ctx.layout:
            ctx.layout.add_command_output(f"{CYAN}{old_provider}{RESET} → {GREEN}{provider}{RESET} | {CYAN}{ctx.client.current_model}{RESET}")
        return True

    def _select_interactive(self, client) -> str | None:
        """交互式选择供应商"""
        choices = []
        for key, pconfig in client.providers.items():
            display = f"{pconfig.name} ({key})"
            if key == client.current_provider:
                display = f"{display} *"
            choices.append((key, display))

        # 添加 "新增 provider" 选项
        choices.append(("__ADD__", "Add new provider..."))

        menu = SelectMenu(
            f"Select provider (current: {client.current_provider})",
            choices,
            client.current_provider,
        )
        result = menu.show()

        if result == "__ADD__":
            return self._add_provider_interactive(client)

        return result

    def _add_provider_interactive(self, client) -> str | None:
        """交互式添加新 provider"""
        wizard = Wizard()

        # Step 1: Provider Key
        key = wizard.step(
            "Provider key (e.g., 'anthropic', 'deepseek')",
            hint="Internal identifier for the provider",
            required=True,
            validator=lambda k: True if k not in client.providers else f"Provider '{k}' already exists",
        )
        if key is None:
            return None

        # Step 2: Display Name
        name = wizard.step(
            "Display name (e.g., 'Anthropic', 'DeepSeek')",
            default=key,
        )
        if wizard.cancelled:
            return None

        # Step 3: Base URL
        base_url = wizard.step(
            "Base URL",
            hint="e.g., 'https://api.anthropic.com/v1'",
            required=True,
        )
        if base_url is None:
            return None

        # Step 4: API Key (optional)
        api_key = wizard.step("API Key (optional, press Enter to skip)")
        if wizard.cancelled:
            return None

        # Step 5: Models
        models_input = wizard.step(
            "Models (comma-separated, e.g., 'claude-3-opus,claude-3-sonnet')",
            hint="Press Enter to skip and add later via /model",
        )
        if wizard.cancelled:
            return None
        models = [m.strip() for m in models_input.split(",") if m.strip()] if models_input else []

        # Add provider
        try:
            client.add_provider(key, name, base_url, api_key, models)
            success(f"Added provider '{key}'")
            return key
        except ValueError as e:
            error(str(e))
            return None

    def _switch_provider(self, ctx: CommandContext, provider_key: str):
        """切换供应商"""
        old_provider = ctx.client.current_provider
        ctx.client.set_provider(provider_key)
        return old_provider
