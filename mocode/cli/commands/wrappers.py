"""CLI interactive wrappers for core commands.

Each wrapper inherits from the core command and adds interactive
selection/confirmation UI. When args are provided, they delegate
directly to the core command.
"""

import os
import shutil

from ...core.commands import (
    CommandContext,
    CommandResult,
    CommandRegistry,
    ModeCommand as CoreModeCommand,
    ProviderCommand as CoreProviderCommand,
    SessionCommand as CoreSessionCommand,
    DreamCommand as CoreDreamCommand,
    SkillsCommand as CoreSkillsCommand,
    CompactCommand as CoreCompactCommand,
    HelpCommand as CoreHelpCommand,
    ClearCommand as CoreClearCommand,
    QuitCommand as CoreQuitCommand,
)
from ...skills.manager import SkillManager
from ...skills.installer import SkillInstaller
from ..ui.prompt import (
    select, ask, confirm, Wizard,
    MenuAction, MenuItem, is_cancelled, is_action,
)
from ..ui.components import MultiSelect
from ..ui.styles import DIM, RESET, GREEN, CYAN, YELLOW, RED, info, success, error
from ..ui.textwrap import truncate_text


# --- Helpers ---

def _render_msg(result: CommandResult) -> None:
    """Render result message to terminal."""
    if result.message:
        (success if result.success else error)(result.message)


def _get_display(ctx: CommandContext):
    return ctx.extra.get("display")


# --- Wrappers ---


class CLIQuitCommand(CoreQuitCommand):
    pass


class CLIClearCommand(CoreClearCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        result = super().execute(ctx)
        os.system("cls" if os.name == "nt" else "clear")
        display = _get_display(ctx)
        if display:
            display.welcome("mocode", ctx.client.config.display_name, os.getcwd())
        return result


class CLIHelpCommand(CoreHelpCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        result = super().execute(ctx)
        if not result.data:
            return result

        commands = result.data["commands"]

        if not ctx.args.strip():
            choices = [
                (c["name"], f'{c["name"]} - {c["description"]}')
                for c in commands
                if c["name"] not in ("/help", "/")
            ]
            choices.append(MenuItem.exit_())
            selected = select("Select command", choices)
            if not is_cancelled(selected):
                cmd = CommandRegistry().get(selected)
                if cmd:
                    ctx.args = ""
                    return cmd.execute(ctx)
            return CommandResult()

        # Text listing
        for c in commands:
            aliases = f" {DIM}({', '.join(c['aliases'])}){RESET}" if c["aliases"] else ""
            print(f"  {DIM}{c['name']}{RESET}{aliases:<12} {c['description']}")
        return CommandResult()


class CLIModeCommand(CoreModeCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args.strip():
            modes = list(ctx.client.config.modes.items())
            current = ctx.client.config.current_mode
            choices = [
                (name, f"{name} - auto-approve: {m.auto_approve}")
                for name, m in modes
            ]
            choices.append(MenuItem.exit_())
            selected = select("Select mode", choices, current=current)
            if not is_cancelled(selected):
                ctx.args = selected
                return super().execute(ctx)
            return CommandResult()

        result = super().execute(ctx)
        _render_msg(result)
        return result


class CLIProviderCommand(CoreProviderCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args.strip():
            return self._interactive_flow(ctx)

        result = super().execute(ctx)
        if result.success and result.data:
            self._render_switch(result.data)
        else:
            _render_msg(result)
        return result

    # --- Interactive flows ---

    def _interactive_flow(self, ctx: CommandContext) -> CommandResult:
        client = ctx.client

        # Select provider
        while True:
            choices = [
                (key, f"{p.name} ({key})") for key, p in client.providers.items()
            ]
            choices += [MenuItem.manage(), MenuItem.exit_()]
            result = select(
                f"Select provider (current: {client.current_provider})",
                choices, current=client.current_provider,
            )
            if is_cancelled(result):
                return CommandResult()
            if is_action(result, MenuAction.MANAGE):
                self._manage_providers(ctx)
                continue
            break

        old = client.current_provider
        client.set_provider(result)

        # Select model
        provider_name = client.providers[client.current_provider].name
        model_choices = [(m, m) for m in client.models]
        model_choices.append(MenuItem.exit_())
        selected_model = select(
            f"Select model [{provider_name}] (current: {client.current_model})",
            model_choices, current=client.current_model,
        )
        if not is_cancelled(selected_model):
            client.set_model(selected_model)

        data = {"old_provider": old, "new_provider": result, "model": client.current_model}
        self._render_switch(data)
        return CommandResult(success=True, message=f"{old} -> {result} | {client.current_model}")

    def _manage_providers(self, ctx: CommandContext) -> None:
        while True:
            result = select("Manage providers", [
                MenuItem.add("Add new provider"), MenuItem.edit(),
                MenuItem.delete(), MenuItem.back(),
            ])
            if is_cancelled(result):
                return
            if is_action(result, MenuAction.ADD):
                self._add_provider(ctx)
            elif is_action(result, MenuAction.EDIT):
                self._edit_provider(ctx)
            elif is_action(result, MenuAction.DELETE):
                self._delete_provider(ctx)

    def _add_provider(self, ctx: CommandContext) -> None:
        client = ctx.client
        wizard = Wizard(title="Add new provider")

        key = wizard.step(
            "Provider key (e.g., 'anthropic', 'deepseek')",
            hint="Internal identifier", required=True,
            validator=lambda k: True if k not in client.providers else f"Provider '{k}' already exists",
        )
        if key is None:
            return
        name = wizard.step("Display name", default=key)
        if wizard.cancelled:
            return
        base_url = wizard.step("Base URL", hint="e.g., 'https://api.anthropic.com/v1'", required=True)
        if base_url is None:
            return
        api_key = wizard.step("API Key (optional, press Enter to skip)")
        if wizard.cancelled:
            return
        models_input = wizard.step("Models (comma-separated)", hint="Press Enter to skip")
        if wizard.cancelled:
            return
        models = [m.strip() for m in models_input.split(",") if m.strip()] if models_input else []

        try:
            client.add_provider(key, name, base_url, api_key, models)
            success(f"Added provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _edit_provider(self, ctx: CommandContext) -> None:
        client = ctx.client
        choices = [(k, f"{p.name} ({k})") for k, p in client.providers.items()]
        choices.append(MenuItem.back())
        key = select("Select provider to edit", choices)
        if is_cancelled(key):
            return

        pconfig = client.providers[key]
        name, base_url, api_key = pconfig.name, pconfig.base_url, pconfig.api_key

        while True:
            api_display = f"{'*' * min(len(api_key), 8) if api_key else '(not set)'} {DIM}(current){RESET}"
            field = select(f"Edit provider '{key}' - Select field", [
                ("name", f"Display name: {name} {DIM}(current){RESET}"),
                ("base_url", f"Base URL: {base_url} {DIM}(current){RESET}"),
                ("api_key", f"API Key: {api_display}"),
                MenuItem.done(), MenuItem.back(),
            ])
            if is_cancelled(field):
                return
            if is_action(field, MenuAction.DONE):
                break
            if field == "name":
                v = ask("Display name", default=name)
                if v is not None:
                    name = v
            elif field == "base_url":
                v = ask("Base URL", default=base_url, required=True)
                if v is not None:
                    base_url = v
            elif field == "api_key":
                v = ask("API Key", default=api_key)
                if v is not None:
                    api_key = v

        try:
            client.update_provider(key, name=name, base_url=base_url, api_key=api_key)
            success(f"Updated provider '{key}'")
        except ValueError as e:
            error(str(e))

    def _delete_provider(self, ctx: CommandContext) -> None:
        client = ctx.client
        choices = []
        for k, p in client.providers.items():
            if len(client.providers) <= 1:
                choices.append(MenuItem.disabled(f"{p.name} ({k}) - cannot delete last provider"))
            else:
                choices.append((k, f"{p.name} ({k})"))
        choices.append(MenuItem.back())

        key = select("Select provider to delete", choices)
        if is_cancelled(key) or is_action(key, MenuAction.DISABLED):
            return
        if confirm(f"Delete '{client.providers[key].name} ({key})'?"):
            try:
                client.remove_provider(key)
                success(f"Deleted provider '{key}'")
            except ValueError as e:
                error(str(e))

    @staticmethod
    def _render_switch(data: dict) -> None:
        print(f"{CYAN}{data['old_provider']}{RESET} -> {GREEN}{data['new_provider']}{RESET} | {CYAN}{data['model']}{RESET}")


class CLISessionCommand(CoreSessionCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        if arg.startswith("restore "):
            result = super().execute(ctx)
            display = _get_display(ctx)
            if result.success and result.data and display:
                session = result.data.get("session")
                if session:
                    display.render_history(session.messages)
            _render_msg(result)
            return result

        sessions = ctx.client.list_sessions()
        if not sessions:
            info("No saved sessions")
            return CommandResult()

        choices = [(s.id, self._format_session(s)) for s in sessions]
        choices.append(MenuItem.exit_())

        selected = select("Select session to restore", choices)
        if selected and not is_cancelled(selected):
            if ctx.client.agent.messages:
                ctx.client.save_session()
            session = ctx.client.load_session(selected)
            display = _get_display(ctx)
            if session:
                if display:
                    display.render_history(session.messages)
                success(f"Restored session: {selected}")
            else:
                error(f"Session not found: {selected}")
        return CommandResult()

    @staticmethod
    def _format_session(session) -> str:
        time_str = session.updated_at[5:16].replace("T", " ") if session.updated_at else "unknown"
        preview = ""
        for msg in session.messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    preview = content[:30] + "..." if len(content) > 30 else content
                break
        text = f'{time_str} "{preview}"' if preview else time_str
        return truncate_text(text, shutil.get_terminal_size().columns - 6)


class CLIDreamCommand(CoreDreamCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        result = super().execute(ctx)

        if result.data:
            action = result.data.get("action")
            if action == "log":
                return self._log_interactive(ctx, result)
            if action == "restore":
                return self._restore_interactive(ctx, result)

        _render_msg(result)
        return result

    def _log_interactive(self, ctx, result: CommandResult) -> CommandResult:
        selected = self._select_snapshot(result.data["snapshots"], "Select a snapshot to view")
        if not selected:
            return CommandResult()

        snap = ctx.client.dream_manager.get_snapshot(selected["id"])
        if not snap:
            error(f"Snapshot not found: {selected['id']}")
            return CommandResult()

        info(f"Snapshot: {snap['id']} ({snap['created_at']})")
        print(f"Trigger: {snap['trigger']}\n")
        for name, content in snap.get("files", {}).items():
            print(f"--- {name} ---")
            lines = content.splitlines()
            for line in lines[:50]:
                print(f"  {line}")
            if len(lines) > 50:
                print(f"  ... ({len(lines) - 50} more lines)")
            print("")
        return CommandResult()

    def _restore_interactive(self, ctx, result: CommandResult) -> CommandResult:
        selected = self._select_snapshot(result.data["snapshots"], "Select snapshot to restore")
        if not selected:
            return CommandResult()
        if not confirm(f"Restore snapshot '{selected['id']}'?"):
            return CommandResult()
        ctx.args = f"restore {selected['id']}"
        result = super().execute(ctx)
        _render_msg(result)
        return result

    @staticmethod
    def _select_snapshot(snapshots, title) -> dict | None:
        choices = [(s["id"], s["created_at"]) for s in snapshots]
        choices.append(MenuItem.exit_())
        selected_id = select(title, choices)
        if is_cancelled(selected_id):
            return None
        return next((s for s in snapshots if s["id"] == selected_id), None)


class CLISkillsCommand(CoreSkillsCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        arg = ctx.args.strip()

        result = self._route_subcommand(ctx, arg, self._SUBCOMMANDS)
        if result is not None:
            if result.data and result.data.get("action") == "install_multi":
                return self._multi_install(ctx, result, SkillInstaller(), self._refresh_skills)
            _render_msg(result)
            return result

        manager = SkillManager.get_instance()
        skills = manager.list_skills()

        if not skills:
            print("No skills available.\nSkills directories:")
            for d in manager.skills_dirs:
                print(f"  {d} {'(exists)' if d.exists() else '(not found)'}")
            return CommandResult()

        if not arg:
            choices = []
            for name in sorted(skills):
                skill = manager.get_skill(name)
                desc = skill.metadata.description if skill else ""
                if len(desc) > 50:
                    desc = desc[:50] + "..."
                choices.append((name, f"{name} - {desc}"))
            choices.append(MenuItem.exit_())
            selected = select("Select a skill to activate", choices)
            if is_cancelled(selected):
                return CommandResult()
            ctx.args = selected

        return super().execute(ctx)


# --- Shared multi-install helper ---

def _multi_install(ctx, result: CommandResult, installer, on_success) -> CommandResult:
    """Handle multi-item repo install with MultiSelect."""
    candidates = result.data["candidates"]
    url = result.data["url"]

    choices = [
        (c["name"], f'{c["name"]} - {c["description"]}' if c.get("description") else c["name"])
        for c in candidates
    ]
    multi = MultiSelect("Select items to install", choices, min_selections=1)
    selected = multi.show()

    if not selected:
        info("Installation cancelled")
        return CommandResult()

    _, candidates_full = installer.discover_from_repo(url)
    selected_candidates = [c for c in candidates_full if c.name in selected]
    results = installer.install_multiple(url, selected_candidates)

    count = sum(1 for r in results if r.success)
    for r in results:
        (success if r.success else error)(
            f"  + {r.item_name}" if r.success else f"  - {r.item_name}: {r.error}"
        )

    if count > 0:
        on_success()
        success(f"Installed {count} item(s)")
    return CommandResult(success=count > 0)


class CLICompactCommand(CoreCompactCommand):

    def execute(self, ctx: CommandContext) -> CommandResult:
        result = super().execute(ctx)

        if result.data and "model" in result.data:
            self._render_status(result.data)
        else:
            _render_msg(result)

        return result

    @staticmethod
    def _render_status(data: dict) -> None:
        info(f"Token usage for {data['model']}:")
        pt, win = data["prompt_tokens"], data["window"]
        pct = (pt / win * 100) if win > 0 else 0
        cfg = data["compact_config"]
        print(f"  Prompt tokens:   {pt:,} / {win:,} ({pct:.1f}%)")
        print(f"  Completion:      {data['completion_tokens']:,}")
        print(f"  Auto-compact at: {cfg['threshold'] * 100:.0f}% ({int(win * cfg['threshold']):,} tokens)")
        print(f"  Keep recent:     {cfg['keep_recent_turns']} turns")
        print(f"  Enabled:         {cfg['enabled']}")
        print(f"  Messages:        {data['message_count']}")
