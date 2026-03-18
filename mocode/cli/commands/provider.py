"""供应商切换命令"""

from .base import Command, CommandContext, command
from ..ui import SelectMenu, Action, navigable, error, success, Wizard, ask, parse_selection_arg
from ..ui.colors import RESET, CYAN, GREEN, YELLOW, RED


@command("/provider", "/p", description="切换供应商")
class ProviderCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if not arg:
            # 使用导航模式的交互式选择
            provider = self._select_interactive_navigable(ctx.client)
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

    @navigable
    def _select_interactive_navigable(self, client) -> str | None:
        """交互式选择供应商 - 导航模式

        使用内部循环保持菜单状态，避免 Action.STAY 导致函数重新执行
        """
        from ..ui.colors import DIM

        while True:
            choices = []
            for key, pconfig in client.providers.items():
                display = f"{pconfig.name} ({key})"
                choices.append((key, display))
            choices.append(("__MANAGE__", f"{DIM}Manage providers...{RESET}"))

            menu = SelectMenu(
                f"Select provider (current: {client.current_provider})",
                choices,
                client.current_provider,
            )
            result = menu.show()

            if result is Action.BACK or result is None:
                # ESC/Back 退出导航
                return None
            elif result == "__MANAGE__":
                # 进入管理子菜单
                sub_result = self._manage_providers(client)
                # 子菜单返回后继续循环（在下方重新显示主菜单）
                continue
            else:
                # 选择了 provider，返回结果
                return result

    def _manage_providers(self, client) -> Action:
        """管理供应商菜单

        使用内部循环保持菜单状态
        """
        from ..ui.colors import DIM

        while True:
            menu = SelectMenu(
                "Manage providers",
                [
                    ("__ADD__", f"{GREEN}Add new provider{RESET}"),
                    ("__EDIT__", f"{YELLOW}Edit provider{RESET}"),
                    ("__DELETE__", f"{RED}Delete provider{RESET}"),
                    ("__BACK__", f"{DIM}← Back{RESET}"),
                ],
            )
            result = menu.show()

            if result is Action.BACK or result is None or result == "__BACK__":
                # 返回上一层级
                return Action.BACK
            elif result == "__ADD__":
                self._add_provider_interactive(client)
                # 操作后继续显示管理菜单
                continue
            elif result == "__EDIT__":
                self._edit_provider_interactive(client)
                continue
            elif result == "__DELETE__":
                self._delete_provider_interactive(client)
                continue
            else:
                # 其他情况，继续显示菜单
                continue

    def _add_provider_interactive(self, client) -> None:
        """交互式添加新 provider"""
        wizard = Wizard(title="Add new provider")

        # Step 1: Provider Key
        key = wizard.step(
            "Provider key (e.g., 'anthropic', 'deepseek')",
            hint="Internal identifier for the provider",
            required=True,
            validator=lambda k: True if k not in client.providers else f"Provider '{k}' already exists",
        )
        if key is None:
            return

        # Step 2: Display Name
        name = wizard.step(
            "Display name (e.g., 'Anthropic', 'DeepSeek')",
            default=key,
        )
        if wizard.cancelled:
            return

        # Step 3: Base URL
        base_url = wizard.step(
            "Base URL",
            hint="e.g., 'https://api.anthropic.com/v1'",
            required=True,
        )
        if base_url is None:
            return

        # Step 4: API Key (optional)
        api_key = wizard.step("API Key (optional, press Enter to skip)")
        if wizard.cancelled:
            return

        # Step 5: Models
        models_input = wizard.step(
            "Models (comma-separated, e.g., 'claude-3-opus,claude-3-sonnet')",
            hint="Press Enter to skip and add later via /model",
        )
        if wizard.cancelled:
            return
        models = [m.strip() for m in models_input.split(",") if m.strip()] if models_input else []

        # Add provider
        try:
            client.add_provider(key, name, base_url, api_key, models)
            success(f"Added provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _edit_provider_interactive(self, client) -> None:
        """交互式编辑 provider - 选择字段编辑模式"""
        from ..ui.colors import DIM

        # 选择要编辑的 provider
        choices = []
        for key, pconfig in client.providers.items():
            display = f"{pconfig.name} ({key})"
            choices.append((key, display))
        choices.append(("__BACK__", f"{DIM}← Back{RESET}"))

        menu = SelectMenu("Select provider to edit", choices)
        key = menu.show()

        if key is None or key == "__BACK__":
            return

        pconfig = client.providers[key]

        # 使用局部变量存储修改
        name = pconfig.name
        base_url = pconfig.base_url
        api_key = pconfig.api_key

        # 编辑字段选择菜单循环
        while True:
            # 构建字段选项，显示当前值（包括未保存的修改）
            name_display = f"{name} {DIM}(current){RESET}"
            base_url_display = f"{base_url} {DIM}(current){RESET}"
            api_key_display = f"{'*' * min(len(api_key), 8) if api_key else '(not set)'} {DIM}(current){RESET}"

            menu = SelectMenu(
                f"Edit provider '{key}' - Select field",
                [
                    ("name", f"Display name: {name_display}"),
                    ("base_url", f"Base URL: {base_url_display}"),
                    ("api_key", f"API Key: {api_key_display}"),
                    ("__DONE__", f"{GREEN}✓ Save and exit{RESET}"),
                    ("__BACK__", f"{DIM}← Cancel{RESET}"),
                ],
            )
            field = menu.show()

            if field is Action.BACK or field is None or field == "__BACK__":
                # 取消编辑
                return
            elif field == "__DONE__":
                # 保存并退出
                break
            elif field == "name":
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

        # 保存修改
        try:
            client.update_provider(
                key,
                name=name,
                base_url=base_url,
                api_key=api_key,
            )
            success(f"Updated provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _delete_provider_interactive(self, client) -> None:
        """交互式删除 provider"""
        from ..ui.colors import DIM, RESET

        # 选择要删除的 provider
        choices = []
        for key, pconfig in client.providers.items():
            # 不能删除最后一个 provider
            if len(client.providers) <= 1:
                display = f"{DIM}{pconfig.name} ({key}) - cannot delete last provider{RESET}"
                choices.append(("__DISABLED__", display))
            else:
                display = f"{pconfig.name} ({key})"
                choices.append((key, display))
        choices.append(("__BACK__", f"{DIM}← Back{RESET}"))

        menu = SelectMenu("Select provider to delete", choices)
        key = menu.show()

        if key is None or key == "__BACK__" or key == "__DISABLED__":
            return

        pconfig = client.providers[key]

        # 确认删除
        confirm_menu = SelectMenu(
            f"{YELLOW}Delete provider '{pconfig.name}' ({key})?{RESET}",
            [
                ("__CONFIRM__", f"{RED}Yes, delete{RESET}"),
                ("__CANCEL__", f"{GREEN}No, cancel{RESET}"),
            ],
        )
        confirm = confirm_menu.show()

        if confirm != "__CONFIRM__":
            return

        # 删除 provider
        try:
            client.remove_provider(key)
            success(f"Deleted provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _switch_provider(self, ctx: CommandContext, provider_key: str):
        """切换供应商"""
        old_provider = ctx.client.current_provider
        ctx.client.set_provider(provider_key)
        return old_provider
