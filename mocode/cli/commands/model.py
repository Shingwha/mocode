"""模型切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, error, success, ask


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

        choices = [(m, m) for m in models]
        # 添加 "新增 model" 选项
        choices.append(("__ADD__", "Add new model..."))

        provider_name = client.providers[client.current_provider].name if client.current_provider in client.providers else client.current_provider
        menu = SelectMenu(f"Select model [{provider_name}] (current: {current})", choices, current)
        result = menu.show()

        if result == "__ADD__":
            return self._add_model_interactive(client)

        return result

    def _add_model_interactive(self, client) -> str | None:
        """交互式添加新 model"""
        model = ask(
            "Model name (e.g., 'gpt-4o', 'claude-3-opus')",
            required=True,
        )
        if model is None:
            return None

        # Add model
        try:
            client.add_model(model)
            success(f"Added model '{model}'")
            return model
        except ValueError as e:
            error(str(e))
            return None

    def _switch_model(self, ctx: CommandContext, model: str, quiet: bool = False):
        """执行模型切换并保存配置"""
        ctx.client.set_model(model)

        if not quiet and ctx.layout:
            from ..ui import format_success
            ctx.layout.add_command_output(format_success(f"Switched to {model}"))
