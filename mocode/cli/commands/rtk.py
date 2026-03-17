"""RTK 命令 - 管理 Rust Token Killer"""

from ..ui.colors import BOLD, DIM, GREEN, RED, RESET, YELLOW
from ..ui.widgets import SelectMenu
from .base import Command, CommandContext, command


@command("/rtk", description="Manage RTK (Token Killer) - compress command output")
class RtkCommand(Command):
    """RTK 管理命令

    子命令:
        status  - 显示 RTK 安装状态
        install - 安装 RTK（仅 Windows 自动安装）
        gain    - 显示 token 节省统计
        enable  - 启用 RTK
        disable - 禁用 RTK
    """

    def execute(self, ctx: CommandContext) -> bool:
        args = ctx.args.split() if ctx.args else []

        # 如果没有参数，显示交互式选择菜单
        if not args:
            return self._show_menu(ctx)

        subcommand = args[0]

        if subcommand == "status":
            self._show_status(ctx)
        elif subcommand == "install":
            self._install(ctx)
        elif subcommand == "gain":
            self._show_gain(ctx)
        elif subcommand == "enable":
            self._set_enabled(ctx, True)
        elif subcommand == "disable":
            self._set_enabled(ctx, False)
        elif subcommand == "help":
            self._show_help(ctx)
        else:
            ctx.layout.add_command_output(f"Unknown subcommand: {subcommand}")
            self._show_help(ctx)

        return True

    def _show_menu(self, ctx: CommandContext) -> bool:
        """显示交互式选择菜单"""
        choices = [
            ("status", "Status - Show RTK installation and feature status"),
            ("gain", "Gain - Show token savings statistics"),
            ("enable", "Enable - Enable RTK feature"),
            ("disable", "Disable - Disable RTK feature"),
            ("install", "Install - Install RTK (auto-install on Windows)"),
        ]

        menu = SelectMenu("RTK (Rust Token Killer)", choices)
        selected = menu.show()

        if selected:
            # 执行选中的子命令
            if selected == "status":
                self._show_status(ctx)
            elif selected == "gain":
                self._show_gain(ctx)
            elif selected == "enable":
                self._set_enabled(ctx, True)
            elif selected == "disable":
                self._set_enabled(ctx, False)
            elif selected == "install":
                self._install(ctx)

        return True

    def _show_status(self, ctx: CommandContext):
        """显示 RTK 状态"""
        from ...tools.rtk_wrapper import check_rtk_installation

        is_installed, message = check_rtk_installation()

        # 安装状态
        if is_installed:
            status = f"{GREEN}[OK] Installed{RESET}"
        else:
            status = f"{YELLOW}[--] Not installed{RESET}"

        # 启用状态
        enabled = ctx.config.rtk.enabled
        enabled_str = (
            f"{GREEN}enabled{RESET}" if enabled else f"{YELLOW}disabled{RESET}"
        )

        ctx.layout.add_command_output(f"RTK Status: {status}")
        ctx.layout.add_command_output(f"RTK Feature: {enabled_str}")

        if not is_installed:
            ctx.layout.add_command_output(f"  {message}")

    def _install(self, ctx: CommandContext):
        """安装 RTK"""
        import platform

        from ...tools.rtk_wrapper import (
            RTK_INSTALL_DIR,
            get_install_command,
            install_rtk,
        )

        ctx.layout.add_command_output("Installing RTK...")

        if install_rtk():
            ctx.layout.add_command_output(
                f"{GREEN}[OK] RTK installed successfully!{RESET}"
            )
            if platform.system() == "Windows":
                ctx.layout.add_command_output(
                    f'Add to PATH: setx PATH "%PATH%;{RTK_INSTALL_DIR}"'
                )
        else:
            # 显示手动安装命令
            cmd = get_install_command()
            ctx.layout.add_command_output(
                f"{YELLOW}Auto-install only supported on Windows.{RESET}\n"
                f"Install manually with: {cmd}"
            )

    def _show_gain(self, ctx: CommandContext):
        """显示 token 节省统计"""
        from ...tools.rtk_wrapper import get_rtk_gain

        gain = get_rtk_gain()
        if gain:
            ctx.layout.add_command_output(f"{gain}")
        else:
            ctx.layout.add_command_output(
                f"{YELLOW}RTK not installed or no statistics available.{RESET}"
            )

    def _set_enabled(self, ctx: CommandContext, enabled: bool):
        """设置 RTK 启用状态"""
        ctx.config.rtk.enabled = enabled
        ctx.config.save()

        status = f"{GREEN}enabled{RESET}" if enabled else f"{YELLOW}disabled{RESET}"
        ctx.layout.add_command_output(f"RTK Feature: {status}")

    def _show_help(self, ctx: CommandContext):
        """显示帮助信息"""
        help_text = f"""{BOLD}RTK (Rust Token Killer){RESET} - Compress command output to save tokens

{BOLD}Usage:{RESET} /rtk <command>

{BOLD}Commands:{RESET}
  status   Show RTK installation and feature status
  install  Install RTK (auto-install on Windows)
  gain     Show token savings statistics
  enable   Enable RTK feature
  disable  Disable RTK feature

{BOLD}Supported commands:{RESET}
  ls, tree, cat, head, tail, find, grep, rg,
  git status, git log, git diff, git show,
  cargo test, cargo build, npm test, pytest, etc.

{BOLD}Configuration:{RESET} ~/.mocode/config.json
"""
        ctx.layout.add_command_output(help_text)
