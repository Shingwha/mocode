"""供应商切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error, info, success
from ..ui.colors import RESET, CYAN, GREEN, BOLD, BLUE, DIM


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
        elif arg.isdigit():
            # 数字选择
            providers = list(ctx.client.providers.keys())
            num = int(arg)
            if 1 <= num <= len(providers):
                provider = providers[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            # 直接指定供应商 key
            if arg in ctx.client.providers:
                provider = arg
            else:
                error(f"Unknown provider: {arg}")
                available = ", ".join(ctx.client.providers.keys())
                info(f"Available: {available}")
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
        # Step 1: Provider Key
        info("Provider key (e.g., 'anthropic', 'deepseek')")
        print(f"{DIM}  Internal identifier for the provider{RESET}")
        try:
            print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
            key = input().strip()
        except (KeyboardInterrupt, EOFError):
            return None

        if not key:
            error("Provider key cannot be empty")
            return None
        if key in client.providers:
            error(f"Provider '{key}' already exists")
            return None

        # Step 2: Display Name
        info("Display name (e.g., 'Anthropic', 'DeepSeek')")
        try:
            print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
            name = input().strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not name:
            name = key

        # Step 3: Base URL
        info("Base URL")
        print(f"{DIM}  e.g., 'https://api.anthropic.com/v1'{RESET}")
        try:
            print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
            base_url = input().strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not base_url:
            error("Base URL cannot be empty")
            return None

        # Step 4: API Key (optional)
        info("API Key (optional, press Enter to skip)")
        try:
            print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
            api_key = input().strip()
        except (KeyboardInterrupt, EOFError):
            return None

        # Step 5: Models
        info("Models (comma-separated, e.g., 'claude-3-opus,claude-3-sonnet')")
        print(f"{DIM}  Press Enter to skip and add later via /model{RESET}")
        try:
            print(f"{BOLD}{BLUE}>{RESET} ", end="", flush=True)
            models_input = input().strip()
        except (KeyboardInterrupt, EOFError):
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

        # 使用 SDK 方法切换供应商
        ctx.client.set_provider(provider_key)

        # 不在这里输出，由调用者控制输出时机
        return old_provider
