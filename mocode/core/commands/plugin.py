"""Plugin management command"""

import asyncio

from .base import Command, CommandContext, command
from .result import CommandResult
from .utils import resolve_selection
from ...plugins import PluginInfo, PluginState
from ...plugins.installer import PluginInstaller
from ...core.installer import SourceType as PluginSourceType
from ...paths import PLUGINS_DIR


@command("/plugin", description="Manage plugins")
class PluginCommand(Command):
    """Plugin management command"""

    _SUBCOMMANDS = {
        "install": "_install",
        "uninstall": "_uninstall",
        "update": "_update",
        "info": "_show_info",
    }

    def __init__(self):
        self._installer = None

    def _get_installer(self) -> PluginInstaller:
        if self._installer is None:
            self._installer = PluginInstaller(plugins_dir=PLUGINS_DIR)
        return self._installer

    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        # Route subcommands: install, uninstall, update, info
        result = self._route_subcommand(ctx, arg, self._SUBCOMMANDS)
        if result is not None:
            return result

        # Default: list and toggle plugins
        plugins = ctx.client.list_plugins()

        if not plugins:
            return CommandResult(message="No plugins discovered.")

        plugin_name = resolve_selection(arg, [p.name for p in plugins])
        if plugin_name:
            return self._toggle_plugin(ctx, plugin_name)

        # No arg and no selection - return plugin list
        return CommandResult(data={"plugins": [self._format_plugin(p) for p in plugins]})

    # --- Subcommand handlers ---

    def _install(self, ctx: CommandContext, url: str) -> CommandResult:
        if not url:
            return CommandResult(success=False, message="Usage: /plugin install <github-url>")

        installer = self._get_installer()

        try:
            source_type, candidates = installer.discover_from_repo(url)

            if not candidates:
                return CommandResult(success=False, message="No valid plugin found in repository")

            if source_type == PluginSourceType.SINGLE:
                candidate = candidates[0]
                result = installer.install(url, candidate=candidate)

                if result.success:
                    if result.already_installed:
                        return CommandResult(
                            message=f"Plugin '{result.item_name}' is already installed"
                        )
                    ctx.client.discover_plugins()
                    return CommandResult(
                        success=True,
                        message=f"Plugin '{result.item_name}' installed successfully",
                    )
                return CommandResult(success=False, message=result.error)

            # Multi-plugin repo - return candidates for interactive selection
            return CommandResult(data={
                "action": "install_multi",
                "candidates": [
                    {"name": c.name, "description": c.description}
                    for c in candidates
                ],
                "url": url,
            })

        except ValueError as e:
            return CommandResult(success=False, message=str(e))
        except Exception as e:
            return CommandResult(success=False, message=f"Installation failed: {e}")

    def _uninstall(self, ctx: CommandContext, name: str) -> CommandResult:
        if not name:
            return CommandResult(success=False, message="Usage: /plugin uninstall <name>")

        installer = self._get_installer()

        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            return CommandResult(success=False, message=f"Plugin '{name}' not found")

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            return CommandResult(
                success=False,
                message=f"Plugin '{name}' was not installed via /plugin install. "
                        "You can manually remove it from ~/.mocode/plugins/",
            )

        # Disable first if enabled
        if plugin_info.state == PluginState.ENABLED:
            loop = ctx.loop
            if loop:
                self._run_async(ctx.client.disable_plugin(name), loop)

        if installer.uninstall(name):
            ctx.client.discover_plugins()
            return CommandResult(success=True, message=f"Plugin '{name}' uninstalled")
        return CommandResult(success=False, message=f"Failed to uninstall '{name}'")

    def _update(self, ctx: CommandContext, name: str) -> CommandResult:
        if not name:
            return CommandResult(success=False, message="Usage: /plugin update <name>")

        installer = self._get_installer()

        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            return CommandResult(success=False, message=f"Plugin '{name}' not found")

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            return CommandResult(
                success=False,
                message=f"Plugin '{name}' was not installed via /plugin install",
            )

        if plugin_info.state == PluginState.ENABLED:
            loop = ctx.loop
            if loop:
                self._run_async(ctx.client.disable_plugin(name), loop)

        result = installer.update(name)

        if result.success:
            ctx.client.discover_plugins()
            return CommandResult(success=True, message=f"Plugin '{name}' updated successfully")
        return CommandResult(success=False, message=result.error)

    # --- Shared helpers ---

    def _toggle_plugin(self, ctx: CommandContext, name: str) -> CommandResult:
        plugin_info = ctx.client.get_plugin_info(name)

        if plugin_info is None:
            return CommandResult(success=False, message=f"Plugin '{name}' not found")

        loop = ctx.loop
        if not loop:
            return CommandResult(success=False, message="No event loop available")

        try:
            if plugin_info.state == PluginState.ENABLED:
                if self._run_async(ctx.client.disable_plugin(name), loop):
                    return CommandResult(success=True, message=f"Plugin '{name}' disabled")
                return CommandResult(
                    success=False,
                    message=f"Failed to disable plugin '{name}': "
                            f"{plugin_info.error or 'Unknown error'}",
                )
            else:
                if plugin_info.state == PluginState.ERROR:
                    return CommandResult(
                        success=False,
                        message=f"Plugin '{name}' has errors: {plugin_info.error}",
                    )

                if self._run_async(ctx.client.enable_plugin(name), loop):
                    return CommandResult(success=True, message=f"Plugin '{name}' enabled")
                return CommandResult(
                    success=False,
                    message=f"Failed to enable plugin '{name}': "
                            f"{plugin_info.error or 'Unknown error'}",
                )
        except Exception as e:
            return CommandResult(success=False, message=f"Error toggling plugin '{name}': {e}")

    def _show_info(self, ctx: CommandContext, name: str) -> CommandResult:
        if not name:
            return CommandResult(success=False, message="Usage: /plugin info <name>")

        plugin_info = ctx.client.get_plugin_info(name)
        if plugin_info is None:
            return CommandResult(success=False, message=f"Plugin '{name}' not found")

        return CommandResult(data={"plugin": self._format_plugin(plugin_info)})

    @staticmethod
    def _run_async(coro, loop: asyncio.AbstractEventLoop):
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)

    # --- Formatting ---

    def _format_plugin(self, info: PluginInfo) -> dict:
        result = {
            "name": info.name,
            "status": info.state.value,
            "path": str(info.path),
        }

        if info.metadata:
            result["version"] = info.metadata.version
            if info.metadata.description:
                result["description"] = info.metadata.description
            if info.metadata.author:
                result["author"] = info.metadata.author
            if info.metadata.dependencies:
                result["dependencies"] = info.metadata.dependencies
            if info.metadata.permissions:
                result["permissions"] = info.metadata.permissions

        if info.has_error:
            result["error"] = info.error

        return result
