"""供应商切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error, info
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

        menu = SelectMenu(
            f"Select provider (current: {client.current_provider})",
            choices,
            client.current_provider,
        )
        return menu.show()

    def _switch_provider(self, ctx: CommandContext, provider_key: str):
        """切换供应商"""
        old_provider = ctx.client.current_provider

        # 使用 SDK 方法切换供应商
        ctx.client.set_provider(provider_key)

        # 不在这里输出，由调用者控制输出时机
        return old_provider
