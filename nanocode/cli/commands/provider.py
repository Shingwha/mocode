"""供应商切换命令"""

from .base import Command, CommandContext, command
from ..ui import Message, SelectMenu, success, error, info
from ..ui.colors import RESET, CYAN, GREEN
from ...providers import AsyncOpenAIProvider


@command("/provider", "/p", description="切换供应商")
class ProviderCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not arg:
            # 交互式选择 provider
            provider = self._select_interactive(ctx.config)
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
            print()  # 空行分隔
            success(f"{CYAN}{old_provider}{RESET} → {GREEN}{provider}{RESET} | {CYAN}{ctx.config.current.model}{RESET}")
            return result
        elif arg.isdigit():
            # 数字选择
            providers = list(ctx.config.providers.keys())
            num = int(arg)
            if 1 <= num <= len(providers):
                provider = providers[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            # 直接指定供应商 key
            if arg in ctx.config.providers:
                provider = arg
            else:
                error(f"Unknown provider: {arg}")
                available = ", ".join(ctx.config.providers.keys())
                info(f"Available: {available}")
                return True

        # 切换供应商（直接指定时立即输出）
        old_provider = self._switch_provider(ctx, provider)
        print()  # 空行分隔
        success(f"{CYAN}{old_provider}{RESET} → {GREEN}{provider}{RESET} | {CYAN}{ctx.config.current.model}{RESET}")
        return True

    def _select_interactive(self, config) -> str | None:
        """交互式选择供应商"""
        choices = []
        for key, pconfig in config.providers.items():
            display = f"{pconfig.name} ({key})"
            if key == config.current.provider:
                display = f"{display} *"
            choices.append((key, display))

        menu = SelectMenu(
            f"Select provider (current: {config.current.provider})",
            choices,
            config.current.provider,
        )
        return menu.show()

    def _switch_provider(self, ctx: CommandContext, provider_key: str):
        """切换供应商"""
        old_provider = ctx.config.current.provider
        ctx.config.current.provider = provider_key

        # 获取新供应商的配置
        pconfig = ctx.config.providers[provider_key]

        # 如果当前模型不在新供应商的模型列表中，切换到第一个可用模型
        if ctx.config.current.model not in pconfig.models and pconfig.models:
            ctx.config.current.model = pconfig.models[0]

        # 更新 Agent provider
        ctx.agent.update_provider(
            AsyncOpenAIProvider(
                api_key=pconfig.api_key,
                model=ctx.config.current.model,
                base_url=pconfig.base_url,
            )
        )

        # 保存配置
        ctx.config.save()
        # 不在这里输出，由调用者控制输出时机
        return old_provider
