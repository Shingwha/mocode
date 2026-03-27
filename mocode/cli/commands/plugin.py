"""Plugin management command"""

import asyncio

from .base import Command, CommandContext, command
from .utils import resolve_selection
from ..ui.prompt import (
    select, ask, confirm, Wizard,
    MenuAction, MenuItem, is_cancelled, is_action,
)
from ..ui.components import MultiSelect
from ..ui.styles import error, success, info, RESET, GREEN, YELLOW, RED, DIM, CYAN
from ...plugins import PluginInfo, PluginState
from ...plugins.installer import PluginInstaller, PluginSourceType
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

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        # Route subcommands: install, uninstall, update, info
        result = self._route_subcommand(ctx, arg, self._SUBCOMMANDS)
        if result is not None:
            return result

        # Default: list and toggle plugins
        plugins = ctx.client.list_plugins()

        if not plugins:
            self._output(ctx, f"{DIM}No plugins discovered.{RESET}")
            return True

        plugin_names = [p.name for p in plugins]
        plugin_name = resolve_selection(
            arg, plugin_names,
            lambda: self._select_interactive(plugins),
        )
        if plugin_name:
            self._toggle_plugin(ctx, plugin_name)

        return True

    # --- Subcommand handlers ---

    def _install(self, ctx: CommandContext, url: str) -> bool:
        if not url:
            self._error(ctx, "Usage: /plugin install <github-url>")
            return True

        installer = self._get_installer()

        try:
            source_type, candidates = installer.discover_from_repo(url)

            if not candidates:
                self._error(ctx, "No valid plugin found in repository")
                return True

            if source_type == PluginSourceType.SINGLE:
                candidate = candidates[0]
                self._info(ctx, f"Installing {candidate.name}...")
                result = installer.install(url, candidate=candidate)

                if result.success:
                    if result.already_installed:
                        self._info(ctx, f"Plugin '{result.plugin_name}' is already installed")
                    else:
                        self._success(ctx, f"Plugin '{result.plugin_name}' installed successfully")
                        ctx.client.discover_plugins()
                else:
                    self._error(ctx, result.error)
                return True

            self._info(ctx, f"Found {len(candidates)} plugins in repository")
            choices = [
                (c.name, f"{c.name} - {c.description}" if c.description else c.name)
                for c in candidates
            ]

            multi = MultiSelect("Select plugins to install", choices, min_selections=1)
            selected = multi.show()

            if not selected:
                self._info(ctx, "Installation cancelled")
                return True

            selected_candidates = [c for c in candidates if c.name in selected]
            results = installer.install_multiple(url, selected_candidates)

            success_count = sum(1 for r in results if r.success)
            for r in results:
                if r.success:
                    self._success(ctx, f"  + {r.plugin_name}")
                else:
                    self._error(ctx, f"  - {r.plugin_name}: {r.error}")

            if success_count > 0:
                ctx.client.discover_plugins()
                self._success(ctx, f"Installed {success_count} plugin(s)")

            return True

        except ValueError as e:
            self._error(ctx, str(e))
            return True
        except Exception as e:
            self._error(ctx, f"Installation failed: {e}")
            return True

    def _uninstall(self, ctx: CommandContext, name: str) -> bool:
        if not name:
            self._error(ctx, "Usage: /plugin uninstall <name>")
            return True

        installer = self._get_installer()

        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            self._error(ctx, f"Plugin '{name}' not found")
            return True

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            self._error(ctx, f"Plugin '{name}' was not installed via /plugin install")
            self._info(ctx, "You can manually remove it from ~/.mocode/plugins/")
            return True

        if not confirm(f"Uninstall plugin '{name}'?"):
            self._info(ctx, "Cancelled")
            return True

        if plugin_info.state == PluginState.ENABLED:
            loop = getattr(ctx.client, "_loop", None)
            if loop:
                self._run_async(ctx.client.disable_plugin(name), loop)

        if installer.uninstall(name):
            self._success(ctx, f"Plugin '{name}' uninstalled")
            ctx.client.discover_plugins()
        else:
            self._error(ctx, f"Failed to uninstall '{name}'")

        return True

    def _update(self, ctx: CommandContext, name: str) -> bool:
        if not name:
            self._error(ctx, "Usage: /plugin update <name>")
            return True

        installer = self._get_installer()

        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            self._error(ctx, f"Plugin '{name}' not found")
            return True

        installed_info = installer.get_installed_info(name)
        if not installed_info:
            self._error(ctx, f"Plugin '{name}' was not installed via /plugin install")
            return True

        self._info(ctx, f"Updating {name}...")

        if plugin_info.state == PluginState.ENABLED:
            loop = getattr(ctx.client, "_loop", None)
            if loop:
                self._run_async(ctx.client.disable_plugin(name), loop)

        result = installer.update(name)

        if result.success:
            self._success(ctx, f"Plugin '{name}' updated successfully")
            ctx.client.discover_plugins()
        else:
            self._error(ctx, result.error)

        return True

    # --- Shared helpers ---

    def _select_interactive(self, plugins: list[PluginInfo]) -> str | None:
        def formatter(p):
            status = self._format_status(p.state)
            version = p.metadata.version if p.metadata else "-"
            description = p.metadata.description if p.metadata else ""
            desc_display = f" - {description}" if description else ""
            return (p.name, f"{p.name} v{version} [{status}]{desc_display}")

        result = self._select_from_list("Select plugin", plugins, formatter)
        return result.name if isinstance(result, PluginInfo) else None

    def _toggle_plugin(self, ctx: CommandContext, name: str) -> None:
        plugin_info = ctx.client.get_plugin_info(name)

        if plugin_info is None:
            self._error(ctx, f"Plugin '{name}' not found")
            return

        loop = getattr(ctx.client, "_loop", None)

        if plugin_info.state == PluginState.ENABLED:
            if loop and self._run_async(ctx.client.disable_plugin(name), loop):
                self._success(ctx, f"Plugin '{name}' disabled")
            else:
                self._error(ctx, f"Failed to disable plugin '{name}': {plugin_info.error or 'Unknown error'}")
        else:
            if plugin_info.state == PluginState.ERROR:
                self._error(ctx, f"Plugin '{name}' has errors: {plugin_info.error}")
                return

            if loop and self._run_async(ctx.client.enable_plugin(name), loop):
                self._success(ctx, f"Plugin '{name}' enabled")
            else:
                self._error(ctx, f"Failed to enable plugin '{name}': {plugin_info.error or 'Unknown error'}")

    def _show_info(self, ctx: CommandContext, name: str) -> bool:
        if not name:
            self._error(ctx, "Usage: /plugin info <name>")
            return True

        plugin_info = ctx.client.get_plugin_info(name)
        if plugin_info is None:
            self._error(ctx, f"Plugin '{name}' not found")
            return True

        lines = self._format_plugin_info(plugin_info)
        self._output(ctx, "\n".join(lines))
        return True

    @staticmethod
    def _run_async(coro, loop: asyncio.AbstractEventLoop):
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)

    # --- Formatting ---

    def _format_plugin_info(self, info: PluginInfo) -> list[str]:
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
        status_map = {
            PluginState.DISCOVERED: f"{DIM}discovered{RESET}",
            PluginState.LOADED: f"{YELLOW}loaded{RESET}",
            PluginState.ENABLED: f"{GREEN}enabled{RESET}",
            PluginState.DISABLED: f"{YELLOW}disabled{RESET}",
            PluginState.ERROR: f"{RED}error{RESET}",
        }
        return status_map.get(state, str(state.value))
