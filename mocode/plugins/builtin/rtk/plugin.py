"""RTK Plugin - Rust Token Killer integration for mocode"""

from mocode.plugins import HookContext, HookPoint, Plugin, PluginMetadata, hook

from .wrapper import check_installation, find_rtk, get_gain, install_rtk, should_wrap, wrap


@hook(HookPoint.TOOL_BEFORE_RUN, name="rtk-wrap", priority=10)
def wrap_bash_with_rtk(context: HookContext) -> HookContext:
    """Wrap bash commands with RTK to save tokens"""
    if context.data.get("name") != "bash":
        return context
    if not find_rtk():
        return context

    args = context.data.get("args", {})
    command = args.get("command", "")

    if command and should_wrap(command):
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
        )

    def get_hooks(self) -> list:
        return [wrap_bash_with_rtk]

    def get_commands(self) -> list:
        return [_create_rtk_command()]


# Command definition at module level
_rtk_command = None


def _create_rtk_command():
    """Create RTK command class lazily."""
    from mocode.cli.commands.base import Command, CommandContext, command
    from mocode.cli.ui.prompt import select, MenuItem, is_cancelled
    from mocode.cli.ui.styles import BOLD, RESET

    @command("/rtk", description="Manage RTK - compress command output")
    class RtkCommand(Command):
        """RTK management command"""

        def execute(self, ctx: CommandContext) -> bool:
            args = ctx.args.split() if ctx.args else []

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
                if ctx.display:
                    ctx.display.error(f"Unknown subcommand: {subcommand}")
                self._show_help(ctx)

            return True

        def _show_menu(self, ctx: CommandContext) -> bool:
            choices = [
                ("status", "Status - Show RTK installation and plugin status"),
                ("gain", "Gain - Show token savings statistics"),
                ("install", "Install - Install RTK (auto-install on Windows)"),
            ]
            choices.append(MenuItem.exit_())

            selected = select("RTK (Rust Token Killer)", choices)

            if not is_cancelled(selected):
                if selected == "status":
                    self._show_status(ctx)
                elif selected == "gain":
                    self._show_gain(ctx)
                elif selected == "install":
                    self._install(ctx)

            return True

        def _show_status(self, ctx: CommandContext):
            from mocode.plugins import PluginState

            is_installed, message = check_installation()
            info = ctx.client.get_plugin_info("rtk")
            is_enabled = info and info.state == PluginState.ENABLED

            if not ctx.display:
                return

            if is_installed and is_enabled:
                ctx.display.success("RTK: Installed, Plugin Enabled")
            elif is_installed and not is_enabled:
                ctx.display.info("RTK: Installed, Plugin Disabled")
            else:
                ctx.display.error("RTK: Not installed")

            if not is_installed:
                ctx.display.command_output(f"  {message}")

        def _install(self, ctx: CommandContext):
            import platform
            from .wrapper import MOCODE_BIN_DIR, get_install_command

            if ctx.display:
                ctx.display.info("Installing RTK...")

            if install_rtk():
                if ctx.display:
                    ctx.display.success("RTK installed successfully!")
                    if platform.system() == "Windows":
                        ctx.display.command_output(
                            f'Add to PATH: setx PATH "%PATH%;{MOCODE_BIN_DIR}"'
                        )
            else:
                cmd = get_install_command()
                if ctx.display:
                    ctx.display.error("Auto-install only supported on Windows.")
                    ctx.display.command_output(f"Install manually with: {cmd}")

        def _show_gain(self, ctx: CommandContext):
            gain = get_gain()
            if ctx.display:
                if gain:
                    ctx.display.info(gain)
                else:
                    ctx.display.error("RTK not installed or no statistics available.")

        def _show_help(self, ctx: CommandContext):
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
            if ctx.display:
                ctx.display.command_output(help_text)

    return RtkCommand()


# Initialize command lazily
_rtk_command = _create_rtk_command()

plugin_class = RtkPlugin
