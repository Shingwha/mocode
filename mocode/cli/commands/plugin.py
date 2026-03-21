"""Plugin management command"""

from .base import Command, CommandContext, command
from .utils import parse_selection_arg
from ..ui import (
    SelectMenu,
    MenuAction,
    MenuItem,
    is_cancelled,
    is_action,
    error,
    success,
    info,
    ask,
)
from ..ui.colors import RESET, GREEN, YELLOW, RED, DIM, CYAN
from ...plugins import PluginInfo, PluginState


@command("/plugin", description="Manage plugins")
class PluginCommand(Command):
    """Plugin management command"""

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        if arg.startswith("list"):
            return self._list_plugins(ctx)
        elif arg.startswith("info "):
            name = arg[5:].strip()
            return self._show_info(ctx, name)
        elif arg == "help":
            return self._show_help(ctx)

        plugins = ctx.client.list_plugins()

        if not plugins:
            if ctx.layout:
                ctx.layout.add_command_output(f"{DIM}No plugins discovered.{RESET}")
            return True

        plugin_names = [p.name for p in plugins]

        if not arg:
            plugin_name = self._select_interactive(ctx.client, plugins)
            if not plugin_name:
                return True
            self._toggle_plugin(ctx, plugin_name)
        else:
            plugin_name = parse_selection_arg(arg, plugin_names, error_handler=error)
            if plugin_name is None:
                return True
            self._toggle_plugin(ctx, plugin_name)

        return True

    def _select_interactive(self, client, plugins: list[PluginInfo]) -> str | None:
        """Interactive plugin selection."""
        while True:
            choices = []
            for info in plugins:
                status = self._format_status(info.state)
                version = info.metadata.version if info.metadata else "-"
                description = info.metadata.description if info.metadata else ""
                desc_display = f" - {description}" if description else ""
                display = f"{info.name} v{version} [{status}]{desc_display}"
                choices.append((info.name, display))

            choices.append(MenuItem.manage())
            choices.append(MenuItem.exit_())

            menu = SelectMenu("Select plugin", choices)
            result = menu.show()

            if is_cancelled(result):
                return None
            if is_action(result, MenuAction.MANAGE):
                self._manage_plugins(client, plugins)
                plugins = client.list_plugins()
                continue
            return result

    def _manage_plugins(self, client, plugins: list[PluginInfo]) -> None:
        """Manage plugins menu."""
        while True:
            menu = SelectMenu(
                "Manage plugins",
                [
                    MenuItem.list_("List all plugins"),
                    MenuItem.info("View plugin info"),
                    MenuItem.back(),
                ],
            )
            result = menu.show()

            if is_cancelled(result):
                return
            if is_action(result, MenuAction.LIST):
                self._print_plugin_list(plugins)
            elif is_action(result, MenuAction.INFO):
                self._select_and_show_info(client, plugins)

    def _select_and_show_info(self, client, plugins: list[PluginInfo]) -> None:
        """Select a plugin and show its info."""
        if not plugins:
            error("No plugins available")
            return

        choices = [(p.name, p.name) for p in plugins]
        choices.append(MenuItem.back())

        menu = SelectMenu("Select plugin to view", choices)
        name = menu.show()

        if is_cancelled(name):
            return

        info = client.get_plugin_info(name)
        if info:
            self._print_plugin_info(info)

    def _toggle_plugin(self, ctx: CommandContext, name: str) -> None:
        """Toggle plugin enabled/disabled state."""
        info = ctx.client.get_plugin_info(name)

        if info is None:
            error(f"Plugin '{name}' not found")
            return

        if info.state == PluginState.ENABLED:
            if ctx.client.disable_plugin(name):
                success(f"Plugin '{name}' disabled")
            else:
                error(f"Failed to disable plugin '{name}': {info.error or 'Unknown error'}")
        else:
            if info.state == PluginState.ERROR:
                error(f"Plugin '{name}' has errors: {info.error}")
                return

            if ctx.client.enable_plugin(name):
                success(f"Plugin '{name}' enabled")
            else:
                error(f"Failed to enable plugin '{name}': {info.error or 'Unknown error'}")

    def _list_plugins(self, ctx: CommandContext) -> bool:
        """List all discovered plugins."""
        plugins = ctx.client.list_plugins()

        if not plugins:
            if ctx.layout:
                ctx.layout.add_command_output(f"{DIM}No plugins discovered.{RESET}")
            return True

        lines = [f"{CYAN}Discovered plugins:{RESET}", "-" * 50]

        for info in plugins:
            status = self._format_status(info.state)
            version = info.metadata.version if info.metadata else "-"
            description = info.metadata.description if info.metadata else ""
            desc_display = f" - {description}" if description else ""
            lines.append(f"  {info.name} v{version} [{status}]{desc_display}")
            if info.has_error:
                lines.append(f"    {RED}Error: {info.error}{RESET}")

        lines.extend(["-" * 50, f"Total: {len(plugins)} plugin(s)"])

        if ctx.layout:
            ctx.layout.add_command_output("\n".join(lines))
        return True

    def _show_info(self, ctx: CommandContext, name: str) -> bool:
        """Show plugin information."""
        if not name:
            error("Usage: /plugin info <name>")
            return True

        info = ctx.client.get_plugin_info(name)

        if info is None:
            error(f"Plugin '{name}' not found")
            return True

        lines = self._format_plugin_info(info)

        if ctx.layout:
            ctx.layout.add_command_output("\n".join(lines))
        return True

    def _print_plugin_list(self, plugins: list[PluginInfo]) -> None:
        """Print plugin list to console."""
        if not plugins:
            info("No plugins discovered")
            return

        print(f"{CYAN}Discovered plugins:{RESET}")
        print("-" * 50)

        for p in plugins:
            status = self._format_status(p.state)
            version = p.metadata.version if p.metadata else "-"
            description = p.metadata.description if p.metadata else ""
            desc_display = f" - {description}" if description else ""
            print(f"  {p.name} v{version} [{status}]{desc_display}")
            if p.has_error:
                print(f"    {RED}Error: {p.error}{RESET}")

        print("-" * 50)
        print(f"Total: {len(plugins)} plugin(s)")

    def _print_plugin_info(self, info: PluginInfo) -> None:
        """Print plugin info to console."""
        for line in self._format_plugin_info(info):
            print(line)

    def _format_plugin_info(self, info: PluginInfo) -> list[str]:
        """Format plugin info for display."""
        lines = [
            f"{CYAN}Plugin: {info.name}{RESET}",
            f"Status: {self._format_status(info.state)}",
            f"Path: {info.path}",
        ]

        if info.metadata:
            lines.append(f"Version: {info.metadata.version}")
            if info.metadata.description:
                lines.append(f"Description: {info.metadata.description}")
            if info.metadata.author:
                lines.append(f"Author: {info.metadata.author}")
            if info.metadata.dependencies:
                lines.append(f"Dependencies: {', '.join(info.metadata.dependencies)}")
            if info.metadata.permissions:
                lines.append(f"Permissions: {', '.join(info.metadata.permissions)}")

        if info.has_error:
            lines.append(f"{RED}Error: {info.error}{RESET}")

        return lines

    def _format_status(self, state: PluginState) -> str:
        """Format plugin status for display with colors."""
        status_map = {
            PluginState.DISCOVERED: f"{DIM}discovered{RESET}",
            PluginState.LOADED: f"{YELLOW}loaded{RESET}",
            PluginState.ENABLED: f"{GREEN}enabled{RESET}",
            PluginState.DISABLED: f"{YELLOW}disabled{RESET}",
            PluginState.ERROR: f"{RED}error{RESET}",
        }
        return status_map.get(state, str(state.value))

    def _show_help(self, ctx: CommandContext) -> bool:
        """Show help message."""
        help_text = f"""{CYAN}Plugin management commands:{RESET}

/plugin              Interactive plugin selection
/plugin <n>          Select by index (toggle enable/disable)
/plugin <name>       Select by name (toggle enable/disable)
/plugin list         List all discovered plugins
/plugin info <name>  Show plugin information
/plugin help         Show this help message"""
        if ctx.layout:
            ctx.layout.add_command_output(help_text)
        return True
