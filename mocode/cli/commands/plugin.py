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
    MultiSelect,
    confirm_dialog,
)
from ..ui.colors import RESET, GREEN, YELLOW, RED, DIM, CYAN
from ...plugins import PluginInfo, PluginState
from ...plugins.installer import PluginInstaller, PluginSourceType
from ...paths import PLUGINS_DIR


@command("/plugin", description="Manage plugins")
class PluginCommand(Command):
    """Plugin management command"""

    def __init__(self):
        self._installer = None

    def _get_installer(self) -> PluginInstaller:
        """Get or create plugin installer."""
        if self._installer is None:
            self._installer = PluginInstaller(plugins_dir=PLUGINS_DIR)
        return self._installer

    def execute(self, ctx: CommandContext) -> bool:
        arg = ctx.args.strip()

        # Install command
        if arg.startswith("install "):
            url = arg[8:].strip()
            return self._install_plugin(ctx, url)
        elif arg == "install":
            error("Usage: /plugin install <github-url>")
            return True

        # Uninstall command
        if arg.startswith("uninstall "):
            name = arg[10:].strip()
            return self._uninstall_plugin(ctx, name)
        elif arg == "uninstall":
            error("Usage: /plugin uninstall <name>")
            return True

        # Update command
        if arg.startswith("update "):
            name = arg[7:].strip()
            return self._update_plugin(ctx, name)
        elif arg == "update":
            error("Usage: /plugin update <name>")
            return True

        # Installed list
        if arg == "installed":
            return self._list_installed(ctx)

        # Existing commands
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

    def _install_plugin(self, ctx: CommandContext, url: str) -> bool:
        """Install plugin from GitHub URL."""
        if not url:
            error("Usage: /plugin install <github-url>")
            return True

        installer = self._get_installer()

        try:
            # Discover plugins in repository
            source_type, candidates = installer.discover_from_repo(url)

            if not candidates:
                error("No valid plugin found in repository")
                return True

            # Single plugin: install directly
            if source_type == PluginSourceType.SINGLE:
                candidate = candidates[0]
                info(f"Installing {candidate.name}...")

                result = installer.install(url, candidate=candidate)

                if result.success:
                    if result.already_installed:
                        info(f"Plugin '{result.plugin_name}' is already installed")
                    else:
                        success(f"Plugin '{result.plugin_name}' installed successfully")
                        # Refresh plugin list
                        ctx.client.discover_plugins()
                else:
                    error(result.error)

                return True

            # Multi-plugin: show selection
            info(f"Found {len(candidates)} plugins in repository")

            choices = [
                (c.name, f"{c.name} - {c.description}" if c.description else c.name)
                for c in candidates
            ]

            multi_select = MultiSelect(
                "Select plugins to install",
                choices,
                min_selections=1,
            )

            selected = multi_select.show()

            if not selected:
                info("Installation cancelled")
                return True

            # Install selected plugins
            selected_candidates = [c for c in candidates if c.name in selected]

            results = installer.install_multiple(url, selected_candidates)

            # Show results
            success_count = sum(1 for r in results if r.success)
            for r in results:
                if r.success:
                    success(f"  + {r.plugin_name}")
                else:
                    error(f"  - {r.plugin_name}: {r.error}")

            if success_count > 0:
                ctx.client.discover_plugins()
                success(f"Installed {success_count} plugin(s)")

            return True

        except ValueError as e:
            error(str(e))
            return True
        except Exception as e:
            error(f"Installation failed: {e}")
            return True

    def _uninstall_plugin(self, ctx: CommandContext, name: str) -> bool:
        """Uninstall a plugin."""
        if not name:
            error("Usage: /plugin uninstall <name>")
            return True

        installer = self._get_installer()

        # Check if plugin exists
        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            error(f"Plugin '{name}' not found")
            return True

        # Check if it was installed via /plugin install
        installed_info = installer.get_installed_info(name)
        if not installed_info:
            error(f"Plugin '{name}' was not installed via /plugin install")
            info("You can manually remove it from ~/.mocode/plugins/")
            return True

        # Confirm uninstall
        if not confirm_dialog(f"Uninstall plugin '{name}'?"):
            info("Cancelled")
            return True

        # Disable first if enabled
        if plugin_info.state == PluginState.ENABLED:
            ctx.client.disable_plugin(name)

        # Uninstall
        if installer.uninstall(name):
            success(f"Plugin '{name}' uninstalled")
            ctx.client.discover_plugins()
        else:
            error(f"Failed to uninstall '{name}'")

        return True

    def _update_plugin(self, ctx: CommandContext, name: str) -> bool:
        """Update a plugin to latest version."""
        if not name:
            error("Usage: /plugin update <name>")
            return True

        installer = self._get_installer()

        # Check if plugin exists
        plugin_info = ctx.client.get_plugin_info(name)
        if not plugin_info:
            error(f"Plugin '{name}' not found")
            return True

        # Check if it was installed via /plugin install
        installed_info = installer.get_installed_info(name)
        if not installed_info:
            error(f"Plugin '{name}' was not installed via /plugin install")
            return True

        info(f"Updating {name}...")

        # Disable first if enabled
        if plugin_info.state == PluginState.ENABLED:
            ctx.client.disable_plugin(name)

        # Update
        result = installer.update(name)

        if result.success:
            success(f"Plugin '{name}' updated successfully")
            ctx.client.discover_plugins()
        else:
            error(result.error)

        return True

    def _list_installed(self, ctx: CommandContext) -> bool:
        """List plugins installed via /plugin install."""
        installer = self._get_installer()
        installed = installer.list_installed()

        if not installed:
            if ctx.layout:
                ctx.layout.add_command_output(f"{DIM}No plugins installed via /plugin install{RESET}")
            return True

        lines = [f"{CYAN}Installed plugins:{RESET}", "-" * 50]

        for info in installed:
            lines.append(f"  {info.name}")
            lines.append(f"    Source: {info.source}")
            lines.append(f"    Version: {info.version}")
            lines.append(f"    Method: {info.method}")

        lines.extend(["-" * 50, f"Total: {len(installed)} plugin(s)"])

        if ctx.layout:
            ctx.layout.add_command_output("\n".join(lines))
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

/plugin                Interactive plugin selection
/plugin <n>            Select by index (toggle enable/disable)
/plugin <name>         Select by name (toggle enable/disable)
/plugin list           List all discovered plugins
/plugin info <name>    Show plugin information
/plugin install <url>  Install plugin from GitHub
/plugin uninstall <name> Uninstall a plugin
/plugin update <name>  Update a plugin to latest version
/plugin installed      List plugins installed via /plugin install
/plugin help           Show this help message

{DIM}GitHub URL formats:
  owner/repo
  https://github.com/owner/repo
  https://github.com/owner/repo/tree/branch{RESET}"""
        if ctx.layout:
            ctx.layout.add_command_output(help_text)
        return True
