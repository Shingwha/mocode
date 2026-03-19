"""RTK Plugin - Rust Token Killer integration for mocode"""

from mocode.plugins import Hook, HookContext, HookPoint, Plugin, PluginMetadata

from .wrapper import check_installation, find_rtk, get_gain, install_rtk, should_wrap, wrap


class RtkHook(Hook):
    """Hook that intercepts bash tool calls and wraps commands with RTK"""

    @property
    def name(self) -> str:
        return "rtk-wrap"

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.TOOL_BEFORE_RUN

    @property
    def priority(self) -> int:
        return 10  # Execute early

    def should_execute(self, context: HookContext) -> bool:
        # Only intercept bash tool calls
        return context.data.get("name") == "bash"

    def execute(self, context: HookContext) -> HookContext:
        # Skip if RTK not installed
        if not find_rtk():
            return context

        args = context.data.get("args", {})
        command = args.get("command", "")

        if should_wrap(command):
            args["command"] = wrap(command)
            context.modified = True

        return context


class RtkPlugin(Plugin):
    """RTK Plugin - Compresses command output to save tokens

    This plugin automatically wraps bash commands with RTK (Rust Token Killer)
    to reduce token consumption by 60-90%.

    Uses blacklist mode: wraps all commands except interactive ones (vim, nano, etc.)
    """

    def __init__(self):
        self.metadata = PluginMetadata(
            name="rtk",
            version="1.0.0",
            description="Compress command output to save tokens",
            author="MoCode",
        )

    def on_load(self) -> None:
        pass

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def get_hooks(self) -> list[Hook]:
        return [RtkHook()]

    def get_commands(self) -> list:
        """Provide /rtk command"""
        from mocode.cli.commands.base import Command, CommandContext, command
        from mocode.cli.ui.colors import BOLD, DIM, RESET
        from mocode.cli.ui.components import format_error, format_info, format_success
        from mocode.cli.ui.widgets import SelectMenu

        @command("/rtk", description="Manage RTK - compress command output")
        class RtkCommand(Command):
            """RTK management command

            Subcommands:
                status  - Show RTK installation status
                install - Install RTK (auto-install on Windows)
                gain    - Show token savings statistics
                enable  - Enable RTK plugin
                disable - Disable RTK plugin
            """

            def execute(self, ctx: CommandContext) -> bool:
                args = ctx.args.split() if ctx.args else []

                # Show interactive menu if no arguments
                if not args:
                    return self._show_menu(ctx)

                subcommand = args[0]

                if subcommand == "status":
                    self._show_status(ctx)
                elif subcommand == "install":
                    self._install(ctx)
                elif subcommand == "gain":
                    self._show_gain(ctx)
                elif subcommand == "help":
                    self._show_help(ctx)
                else:
                    ctx.layout.add_command_output(format_error(f"Unknown subcommand: {subcommand}"))
                    self._show_help(ctx)

                return True

            def _show_menu(self, ctx: CommandContext) -> bool:
                """Show interactive selection menu"""
                choices = [
                    ("status", "Status - Show RTK installation and plugin status"),
                    ("gain", "Gain - Show token savings statistics"),
                    ("install", "Install - Install RTK (auto-install on Windows)"),
                    ("__EXIT__", f"{DIM}← Cancel{RESET}"),
                ]

                menu = SelectMenu("RTK (Rust Token Killer)", choices)
                selected = menu.show()

                if selected and selected != "__EXIT__":
                    # Execute selected subcommand
                    if selected == "status":
                        self._show_status(ctx)
                    elif selected == "gain":
                        self._show_gain(ctx)
                    elif selected == "install":
                        self._install(ctx)

                return True

            def _show_status(self, ctx: CommandContext):
                """Show RTK status"""
                from mocode.plugins import PluginState

                is_installed, message = check_installation()

                # Get plugin state from client's plugin manager
                plugin_manager = ctx.client.plugin_manager
                is_enabled = False
                if plugin_manager:
                    info = plugin_manager.get_plugin_info("rtk")
                    is_enabled = info and info.state == PluginState.ENABLED

                if is_installed and is_enabled:
                    ctx.layout.add_command_output(format_success("RTK: Installed, Plugin Enabled"))
                elif is_installed and not is_enabled:
                    ctx.layout.add_command_output(format_info("RTK: Installed, Plugin Disabled"))
                else:
                    ctx.layout.add_command_output(format_error("RTK: Not installed"))

                if not is_installed:
                    ctx.layout.add_command_output(f"  {message}")

            def _install(self, ctx: CommandContext):
                """Install RTK"""
                import platform

                from .wrapper import RTK_INSTALL_DIR, get_install_command

                ctx.layout.add_command_output(format_info("Installing RTK..."))

                if install_rtk():
                    ctx.layout.add_command_output(format_success("RTK installed successfully!"))
                    if platform.system() == "Windows":
                        ctx.layout.add_command_output(
                            f'Add to PATH: setx PATH "%PATH%;{RTK_INSTALL_DIR}"'
                        )
                else:
                    # Show manual install command
                    cmd = get_install_command()
                    ctx.layout.add_command_output(
                        format_error("Auto-install only supported on Windows.")
                    )
                    ctx.layout.add_command_output(f"Install manually with: {cmd}")

            def _show_gain(self, ctx: CommandContext):
                """Show token savings statistics"""
                gain = get_gain()
                if gain:
                    ctx.layout.add_command_output(format_info(gain))
                else:
                    ctx.layout.add_command_output(
                        format_error("RTK not installed or no statistics available.")
                    )

            def _show_help(self, ctx: CommandContext):
                """Show help information"""
                help_text = f"""{BOLD}RTK (Rust Token Killer){RESET} - Compress command output to save tokens

{BOLD}Usage:{RESET} /rtk <command>

{BOLD}Commands:{RESET}
  status   Show RTK installation and plugin status
  install  Install RTK (auto-install on Windows)
  gain     Show token savings statistics

{BOLD}Behavior:{RESET}
  RTK wraps all bash commands except interactive ones (vim, nano, less, etc.)
  This reduces token consumption by 60-90%.

{BOLD}Plugin control:{RESET} Use /plugin to enable/disable RTK

{BOLD}GitHub:{RESET} https://github.com/rtk-ai/rtk
"""
                ctx.layout.add_command_output(help_text)

        return [RtkCommand()]
