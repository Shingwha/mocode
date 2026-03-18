"""模型切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, Action, navigable, error, success, ask
from ..ui.colors import RESET, GREEN, YELLOW, RED


@command("/model", "/m", description="切换模型")
class ModelCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()
        quiet = arg == "--quiet"

        if not arg or quiet:
            # 使用导航模式的交互式选择
            model = self._select_interactive_navigable(ctx.client)
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

    @navigable
    def _select_interactive_navigable(self, client) -> str | None:
        """交互式选择模型 - 导航模式

        使用内部循环保持菜单状态
        """
        from ..ui.colors import DIM

        while True:
            models = client.models
            current = client.current_model
            provider_key = client.current_provider

            choices = [(m, m) for m in models]
            choices.append(("__MANAGE__", f"{DIM}Manage models...{RESET}"))

            provider_name = client.providers[provider_key].name if provider_key in client.providers else provider_key
            menu = SelectMenu(f"Select model [{provider_name}] (current: {current})", choices, current)
            result = menu.show()

            if result is Action.BACK or result is None:
                # ESC/Back 退出导航
                return None
            elif result == "__MANAGE__":
                # 进入管理子菜单
                sub_result = self._manage_models(client)
                # 子菜单返回后继续循环（在下方重新显示主菜单）
                continue
            else:
                # 选择了模型，返回结果
                return result

    def _manage_models(self, client) -> Action:
        """管理模型菜单

        使用内部循环保持菜单状态
        """
        from ..ui.colors import DIM

        while True:
            menu = SelectMenu(
                "Manage models",
                [
                    ("__ADD__", f"{GREEN}Add new model{RESET}"),
                    ("__DELETE__", f"{RED}Delete model{RESET}"),
                    ("__BACK__", f"{DIM}← Back{RESET}"),
                ],
            )
            result = menu.show()

            if result is Action.BACK or result is None or result == "__BACK__":
                # 返回上一层级
                return Action.BACK
            elif result == "__ADD__":
                self._add_model_interactive(client)
                # 操作后继续显示管理菜单
                continue
            elif result == "__DELETE__":
                self._delete_model_interactive(client)
                continue
            else:
                # 其他情况，继续显示菜单
                continue

    def _add_model_interactive(self, client) -> None:
        """交互式添加新 model"""
        model = ask(
            "Model name (e.g., 'gpt-4o', 'claude-3-opus')",
            required=True,
        )
        if model is None:
            return

        # Add model
        try:
            client.add_model(model)
            success(f"Added model '{model}'")
        except ValueError as e:
            error(str(e))

    def _delete_model_interactive(self, client) -> None:
        """交互式删除 model"""
        from ..ui.colors import DIM, RESET

        models = client.models
        provider_key = client.current_provider

        if not models:
            error("No models to delete")
            return

        # 选择要删除的 model
        choices = []
        for m in models:
            # 如果只有一个 model，标记为不可删除
            if len(models) <= 1:
                display = f"{DIM}{m} - cannot delete last model{RESET}"
                choices.append(("__DISABLED__", display))
            else:
                choices.append((m, m))
        choices.append(("__BACK__", f"{DIM}← Back{RESET}"))

        provider_name = client.providers[provider_key].name if provider_key in client.providers else provider_key
        menu = SelectMenu(f"Select model to delete [{provider_name}]", choices)
        model = menu.show()

        if model is None or model == "__BACK__" or model == "__DISABLED__":
            return

        # 确认删除
        confirm_menu = SelectMenu(
            f"{YELLOW}Delete model '{model}'?{RESET}",
            [
                ("__CONFIRM__", f"{RED}Yes, delete{RESET}"),
                ("__CANCEL__", f"{GREEN}No, cancel{RESET}"),
            ],
        )
        confirm = confirm_menu.show()

        if confirm != "__CONFIRM__":
            return

        # 删除 model
        try:
            client.remove_model(model)
            success(f"Deleted model '{model}'")
        except ValueError as e:
            error(str(e))

    def _switch_model(self, ctx: CommandContext, model: str, quiet: bool = False):
        """执行模型切换并保存配置"""
        ctx.client.set_model(model)

        if not quiet and ctx.layout:
            from ..ui import format_success
            ctx.layout.add_command_output(format_success(f"Switched to {model}"))
