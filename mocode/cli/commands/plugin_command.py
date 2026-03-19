"""Plugin management command"""

from .base import Command, CommandContext, command
from ...plugins import PluginInfo, PluginState


@command("/plugin", description="Manage plugins")
class PluginCommand(Command):
    """Plugin management command"""

    def execute(self, ctx: CommandContext) -> bool:
        """Execute the plugin command"""
        args = ctx.args.strip()

        if not args:
            return self._show_help(ctx)

        parts = args.split(maxsplit=1)
        subcommand = parts[0].lower()
        subargs = parts[1] if len(parts) > 1 else ""

        if subcommand == "list":
            return self._list_plugins(ctx)
        elif subcommand == "enable":
            return self._enable_plugin(ctx, subargs)
        elif subcommand == "disable":
            return self._disable_plugin(ctx, subargs)
        elif subcommand == "info":
            return self._show_info(ctx, subargs)
        elif subcommand == "help":
            return self._show_help(ctx)
        else:
            return self._show_help(ctx)

    def _show_help(self, ctx: CommandContext) -> bool:
        """Show help message"""
        help_text = """Plugin management commands:

/plugin list              List all discovered plugins
/plugin enable <name>     Enable a plugin
/plugin disable <name>    Disable a plugin
/plugin info <name>       Show plugin information
/plugin help              Show this help message"""
        print(help_text)
        return True

    def _list_plugins(self, ctx: CommandContext) -> bool:
        """List all discovered plugins"""
        plugins = ctx.client.list_plugins()

        if not plugins:
            print("No plugins discovered.")
            return True

        print("Discovered plugins:")
        print("-" * 50)

        for info in plugins:
            status = self._format_status(info.state)
            version = info.metadata.version if info.metadata else "-"
            description = info.metadata.description if info.metadata else ""
            desc_display = f" - {description}" if description else ""

            print(f"  {info.name} v{version} [{status}]{desc_display}")

            if info.has_error:
                print(f"    Error: {info.error}")

        print("-" * 50)
        print(f"Total: {len(plugins)} plugin(s)")
        return True

    def _enable_plugin(self, ctx: CommandContext, name: str) -> bool:
        """Enable a plugin"""
        name = name.strip()

        if not name:
            print("Usage: /plugin enable <name>")
            return True

        # Check if plugin exists
        info = ctx.client.get_plugin_info(name)
        if info is None:
            print(f"Plugin '{name}' not found.")
            return True

        if info.state == PluginState.ENABLED:
            print(f"Plugin '{name}' is already enabled.")
            return True

        success = ctx.client.enable_plugin(name)

        if success:
            print(f"Plugin '{name}' enabled successfully.")
        else:
            error = info.error or "Unknown error"
            print(f"Failed to enable plugin '{name}': {error}")

        return True

    def _disable_plugin(self, ctx: CommandContext, name: str) -> bool:
        """Disable a plugin"""
        name = name.strip()

        if not name:
            print("Usage: /plugin disable <name>")
            return True

        # Check if plugin exists
        info = ctx.client.get_plugin_info(name)
        if info is None:
            print(f"Plugin '{name}' not found.")
            return True

        if info.state != PluginState.ENABLED:
            print(f"Plugin '{name}' is not enabled.")
            return True

        success = ctx.client.disable_plugin(name)

        if success:
            print(f"Plugin '{name}' disabled successfully.")
        else:
            print(f"Failed to disable plugin '{name}'.")

        return True

    def _show_info(self, ctx: CommandContext, name: str) -> bool:
        """Show plugin information"""
        name = name.strip()

        if not name:
            print("Usage: /plugin info <name>")
            return True

        info = ctx.client.get_plugin_info(name)

        if info is None:
            print(f"Plugin '{name}' not found.")
            return True

        print(f"Plugin: {info.name}")
        print(f"Status: {self._format_status(info.state)}")
        print(f"Path: {info.path}")

        if info.metadata:
            print(f"Version: {info.metadata.version}")
            if info.metadata.description:
                print(f"Description: {info.metadata.description}")
            if info.metadata.author:
                print(f"Author: {info.metadata.author}")
            if info.metadata.dependencies:
                print(f"Dependencies: {', '.join(info.metadata.dependencies)}")
            if info.metadata.permissions:
                print(f"Permissions: {', '.join(info.metadata.permissions)}")

        if info.has_error:
            print(f"Error: {info.error}")

        return True

    def _format_status(self, state: PluginState) -> str:
        """Format plugin status for display"""
        status_map = {
            PluginState.DISCOVERED: "discovered",
            PluginState.LOADED: "loaded",
            PluginState.ENABLED: "enabled",
            PluginState.DISABLED: "disabled",
            PluginState.ERROR: "error",
        }
        return status_map.get(state, str(state.value))
