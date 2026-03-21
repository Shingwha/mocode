"""Ripgrep Plugin - Use ripgrep for faster search"""

from mocode.plugins import HookBase, HookContext, HookPoint, Plugin, PluginMetadata

from .wrapper import check_installation, find_ripgrep, install_ripgrep, run_ripgrep


class RipgrepHook(HookBase):
    """Intercept grep tool calls and use ripgrep instead"""

    _name = "ripgrep-intercept"
    _hook_point = HookPoint.TOOL_BEFORE_RUN
    _priority = 5  # High priority

    def should_execute(self, context: HookContext) -> bool:
        return context.data.get("name") == "grep"

    def execute(self, context: HookContext) -> HookContext:
        # Check if ripgrep is available
        if not find_ripgrep():
            return context  # Fallback to built-in grep

        args = context.data.get("args", {})
        pattern = args.get("pat", "")
        path = args.get("path", ".")
        limit = args.get("limit", 100)

        result = run_ripgrep(pattern, path, limit)
        if result is None:
            return context  # Error, fallback to built-in grep

        # Set result and stop original tool execution
        context.set_result(result)
        context.stop_propagation()
        return context


class RipgrepPlugin(Plugin):
    """Ripgrep Plugin - 10-100x faster search

    This plugin intercepts grep tool calls and uses ripgrep instead.
    Falls back gracefully to built-in grep if ripgrep is unavailable.
    """

    def __init__(self):
        self.metadata = PluginMetadata(
            name="ripgrep",
            version="1.0.0",
            description="Use ripgrep for faster grep (10-100x speedup)",
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

    def get_hooks(self) -> list[HookBase]:
        return [RipgrepHook()]

    def get_commands(self) -> list:
        """Provide /ripgrep command"""
        from mocode.cli.commands.base import Command, CommandContext, command
        from mocode.cli.ui import SelectMenu, MenuItem, is_cancelled
        from mocode.cli.ui import format_error, format_info, format_success
        from mocode.cli.ui.colors import BOLD, RESET

        @command("/ripgrep", "/rg", description="Manage ripgrep - faster search")
        class RipgrepCommand(Command):
            """Ripgrep management command

            Subcommands:
                status  - Show ripgrep installation status
                install - Show installation instructions
                enable  - Enable ripgrep plugin
                disable - Disable ripgrep plugin
            """

            def execute(self, ctx: CommandContext) -> bool:
                args = ctx.args.split() if ctx.args else []

                if not args:
                    return self._show_menu(ctx)

                subcommand = args[0]

                if subcommand == "status":
                    self._show_status(ctx)
                elif subcommand == "install":
                    self._show_install(ctx)
                elif subcommand == "enable":
                    self._enable(ctx)
                elif subcommand == "disable":
                    self._disable(ctx)
                elif subcommand == "help":
                    self._show_help(ctx)
                else:
                    ctx.layout.add_command_output(format_error(f"Unknown subcommand: {subcommand}"))
                    self._show_help(ctx)

                return True

            def _show_menu(self, ctx: CommandContext) -> bool:
                """Show interactive selection menu"""
                from mocode.plugins import PluginState

                # Get current state
                info = ctx.client.get_plugin_info("ripgrep")
                is_enabled = info and info.state == PluginState.ENABLED

                choices = [
                    ("status", "Status - Show ripgrep installation and plugin status"),
                    ("install", "Install - Show installation instructions"),
                ]

                # Add enable/disable based on current state
                if is_enabled:
                    choices.append(("disable", "Disable - Turn off ripgrep plugin"))
                else:
                    choices.append(("enable", "Enable - Turn on ripgrep plugin"))

                choices.append(MenuItem.exit_())

                menu = SelectMenu("Ripgrep (Fast Search)", choices)
                selected = menu.show()

                if not is_cancelled(selected):
                    if selected == "status":
                        self._show_status(ctx)
                    elif selected == "install":
                        self._show_install(ctx)
                    elif selected == "enable":
                        self._enable(ctx)
                    elif selected == "disable":
                        self._disable(ctx)

                return True

            def _show_status(self, ctx: CommandContext):
                """Show ripgrep status"""
                from mocode.plugins import PluginState

                is_installed, message = check_installation()

                # Get plugin state via client API
                info = ctx.client.get_plugin_info("ripgrep")
                is_enabled = info and info.state == PluginState.ENABLED

                if is_installed and is_enabled:
                    ctx.layout.add_command_output(format_success(f"Ripgrep: {message}, Plugin Enabled"))
                elif is_installed and not is_enabled:
                    ctx.layout.add_command_output(format_info(f"Ripgrep: {message}, Plugin Disabled"))
                else:
                    ctx.layout.add_command_output(format_error("Ripgrep: Not installed"))

                if not is_installed:
                    ctx.layout.add_command_output(f"  {message}")

            def _show_install(self, ctx: CommandContext):
                """Show installation instructions or auto-install"""
                import platform

                from .wrapper import MOCODE_BIN_DIR, get_install_command

                ctx.layout.add_command_output(format_info("Installing ripgrep..."))

                if install_ripgrep():
                    ctx.layout.add_command_output(format_success("ripgrep installed successfully!"))
                    if platform.system() == "Windows":
                        ctx.layout.add_command_output(
                            f'Add to PATH: setx PATH "%PATH%;{MOCODE_BIN_DIR}"'
                        )
                else:
                    cmd = get_install_command()
                    ctx.layout.add_command_output(
                        format_error("Auto-install only supported on Windows.")
                    )
                    ctx.layout.add_command_output(f"Install manually with: {cmd}")

            def _enable(self, ctx: CommandContext):
                """Enable ripgrep plugin"""
                ctx.client.enable_plugin("ripgrep")
                ctx.layout.add_command_output(format_success("Ripgrep plugin enabled"))

            def _disable(self, ctx: CommandContext):
                """Disable ripgrep plugin"""
                ctx.client.disable_plugin("ripgrep")
                ctx.layout.add_command_output(format_info("Ripgrep plugin disabled"))

            def _show_help(self, ctx: CommandContext):
                """Show help information"""
                help_text = f"""{BOLD}Ripgrep{RESET} - Use ripgrep for faster search (10-100x speedup)

{BOLD}Usage:{RESET} /ripgrep <command>

{BOLD}Commands:{RESET}
  status   Show ripgrep installation and plugin status
  install  Show installation instructions
  enable   Enable ripgrep plugin
  disable  Disable ripgrep plugin

{BOLD}Behavior:{RESET}
  Intercepts grep tool calls and uses ripgrep instead.
  Falls back to built-in grep if ripgrep is unavailable.

{BOLD}Plugin control:{RESET} Use /plugin to manage all plugins

{BOLD}GitHub:{RESET} https://github.com/BurntSushi/ripgrep
"""
                ctx.layout.add_command_output(help_text)

        return [RipgrepCommand()]
