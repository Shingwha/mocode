"""模型切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error


@command("/model", "/m", description="切换模型")
class ModelCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        quiet = arg == "--quiet"

        if not arg or quiet:
            # 交互式选择
            model = self._select_interactive(ctx.client)
            if not model:
                return True
        elif arg.isdigit():
            # 数字选择
            models = ctx.client.models
            num = int(arg)
            if 1 <= num <= len(models):
                model = models[num - 1]
            else:
                error(f"Invalid choice: {num}")
                return True
        else:
            # 直接指定模型名
            model = arg

        # 切换模型
        self._switch_model(ctx, model, quiet=quiet)
        return True

    def _select_interactive(self, client) -> str | None:
        """交互式选择模型"""
        models = client.models
        current = client.current_model

        if not models:
            error("No models available for current provider")
            return None

        choices = [(m, m) for m in models]
        provider_name = client.providers[client.current_provider].name if client.current_provider in client.providers else client.current_provider
        menu = SelectMenu(f"Select model [{provider_name}] (current: {current})", choices, current)
        return menu.show()

    def _switch_model(self, ctx: CommandContext, model: str, quiet: bool = False):
        """执行模型切换并保存配置"""
        ctx.client.set_model(model)

        if not quiet and ctx.layout:
            from ..ui import format_success
            ctx.layout.add_command_output(format_success(f"Switched to {model}"))