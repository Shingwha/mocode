"""CLI application — CLIApp class encapsulates all CLI logic."""

import asyncio
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

from ..config import Config, ProviderConfig, ProviderInfo
from ..session import FileSessionStore, SessionManager
from ...core import Agent
from ...core.skill import SkillManager
from ...core.tool import ToolRegistry
from ...hooks import CompactHook, GoalHook
from ...prompts.app import build_system_prompt
from ...providers.openai import OpenAIProvider
from ...tools import (
    AppendTool,
    BashTool,
    CompactTool,
    EditTool,
    FetchTool,
    GlobTool,
    GoalTool,
    GrepTool,
    ImageTool,
    ReadTool,
    SkillTool,
    SubAgentTool,
    WriteTool,
)
from .display import Display
from .hook import CLIDisplayHook
from .prompts import Choice, confirm, select, text_input


class CLIApp:
    """Interactive CLI application — composable entry point for MoCode."""

    HOME = Path.home() / ".mocode"

    def __init__(self, config: Config | None = None, display: Display | None = None):
        self._fix_console()
        self.config = config or Config.load()
        if not self.config:
            raise SystemExit("Config not found. Create ~/.mocode/config.json first.")
        self.display = display or Display()
        self.agent = self._build_agent()
        self._session_mgr = SessionManager(
            workdir=str(Path.cwd()),
            store=FileSessionStore(),
        )
        self._session_mgr.create()

    # ── Console setup ─────────────────────────────────────

    @staticmethod
    def _fix_console():
        """Enable ANSI escape codes on Windows."""
        if sys.platform != "win32":
            return
        import ctypes
        try:
            ctypes.windll.kernel32.SetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )
        except Exception:
            pass

    # ── Agent construction ─────────────────────────────────

    def _build_agent(self):
        """Build the AgentLoop with tools, hooks, and prompt. Override to customize."""
        pc = self.config.current

        provider = OpenAIProvider(
            api_key=pc.api_key, model=pc.model,
            base_url=pc.base_url, extra_body=pc.extra_body,
        )

        self._tools = ToolRegistry()
        for t in [
            ReadTool(), WriteTool(), AppendTool(), EditTool(),
            GlobTool(), GrepTool(), BashTool(), FetchTool(),
        ]:
            self._tools.register(t)

        ic = self.config.image
        if ic.enabled:
            self._tools.register(ImageTool(base_url=ic.base_url, api_key=ic.api_key, model=ic.model))

        self._skill_mgr = SkillManager([self.HOME / "skills"])
        self._tools.register(SkillTool(self._skill_mgr))

        prompt = self._build_prompt()

        compact_hook = CompactHook(provider)
        goal_hook = GoalHook()

        agent = (
            Agent()
            .provider(provider)
            .prompt(prompt)
            .tools(self._tools)
            .hooks([CLIDisplayHook(self.display), compact_hook, goal_hook])
            .build()
        )

        self._tools.register(CompactTool(provider, lambda: agent.messages))
        self._tools.register(
            SubAgentTool(lambda: agent.provider, self._tools, tool_timeout=agent.config.tool_timeout)
        )
        self._tools.register(GoalTool(goal_hook))

        return agent

    def _build_prompt(self) -> str:
        """Build system prompt — re-reads AGENTS.md each time."""
        return build_system_prompt(
            tools=self._tools, skill_manager=self._skill_mgr, cwd=str(Path.cwd()),
            home=str(self.HOME),
            config_path=str(self.HOME / "config.json"),
            skills_dir=str(self.HOME / "skills"),
            sessions_dir=str(self.HOME / "sessions"),
        )

    # ── Slash commands ─────────────────────────────────────

    def _save_session(self):
        """Persist current messages to session store. Skips empty sessions."""
        if not self.agent.messages:
            return
        pc = self.config.current
        self._session_mgr.save(
            self.agent.messages,
            model=pc.model if pc else "",
            provider=self.config.provider,
        )

    def _export(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"session_{ts}.json"
        path.write_text(
            json.dumps(self.agent.messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.display.info(f"Exported {len(self.agent.messages)} msgs → {path}")

    def _clear(self):
        """Save current session, clear messages, start fresh."""
        self._save_session()
        self.agent.messages.clear()
        self._session_mgr.clear()
        self._session_mgr.create()
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()
        self.display.info("Session saved and cleared.")

    async def _resume(self, arg: str):
        """Resume a session — interactive picker when no arg, direct when arg given."""
        sessions = self._session_mgr.list()

        if not sessions:
            self.display.info("No sessions found.")
            return

        # Direct resume when arg provided (index, ID, or file path)
        if arg:
            # Try file path first
            path = Path(arg.strip('"').strip("'")).expanduser()
            if path.suffix == ".json" and path.exists():
                try:
                    messages = json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:
                    self.display.error(f"Failed to read JSON: {e}")
                    return
                if not isinstance(messages, list):
                    self.display.error("Invalid format: expected a JSON array of messages")
                    return
                self._save_session()
                self.agent.messages.clear()
                self.agent.messages.extend(messages)
                self.agent.system_prompt = self._build_prompt()
                self.display.clear_screen()
                self.display.render_messages(messages)
                user_count = sum(1 for m in messages if m.get("role") == "user")
                self.display.info(f"Resumed {len(messages)} msgs ({user_count} user turns) from {path.name}")
                return

            # Resolve target: numeric index (1-based) or session ID
            target_id = None
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(sessions):
                    target_id = sessions[idx].id
                else:
                    self.display.error(f"Invalid index: {arg}. Use 1-{len(sessions)}.")
                    return
            else:
                target_id = arg

            # Save current before switching
            self._save_session()

            # Resume target
            session = self._session_mgr.resume(target_id)
            if session is None:
                self.display.error(f"Session not found: {target_id}")
                return

            self.agent.messages.clear()
            self.agent.messages.extend(session.messages)
            self.agent.system_prompt = self._build_prompt()
            self.display.clear_screen()
            self.display.render_messages(session.messages)
            user_count = sum(1 for m in session.messages if m.get("role") == "user")
            self.display.info(
                f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
            )
            return

        # Interactive picker when no arg
        active_id = self._session_mgr.active_id
        choices = [
            Choice(
                title=(s.title or "Untitled")[:60],
                value=s.id,
                description=f"{s.updated_at[:10]} · {len(s.messages)} msgs",
            )
            for s in sessions
            if s.id != active_id
        ]
        if not choices:
            self.display.info("No other sessions to resume.")
            return

        chosen = await select("Resume a session:", choices)
        if chosen is None:
            return

        self._save_session()
        session = self._session_mgr.resume(chosen)
        if session is None:
            self.display.error(f"Session not found: {chosen}")
            return

        self.agent.messages.clear()
        self.agent.messages.extend(session.messages)
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()
        self.display.render_messages(session.messages)
        user_count = sum(1 for m in session.messages if m.get("role") == "user")
        self.display.info(
            f"Resumed {session.id} ({len(session.messages)} msgs, {user_count} user turns)"
        )

    async def _model(self):
        """Interactive provider + model picker."""
        info_map = self.config.provider_info
        # Build provider choices — include all providers from config.
        provider_choices = []
        for key, pc in self.config.providers.items():
            info = info_map.get(key)
            title = info.name if info and info.name else key
            preview = ", ".join(info.models) if info and info.models else pc.model
            provider_choices.append(
                Choice(title=title, value=key, description=preview)
            )

        if not provider_choices:
            self.display.warn("No providers configured.")
            return

        chosen_key = await select(
            "Select a provider:",
            provider_choices,
            default=self.config.provider,
        )
        if chosen_key is None:
            return

        info = info_map.get(chosen_key)
        models = info.models if info and info.models else []
        # Skip model picker if there's only one (or zero) models.
        if len(models) <= 1:
            self._switch_to(chosen_key, models[0] if models else self.config.providers[chosen_key].model)
            return

        pc = self.config.providers[chosen_key]
        model_choices = [
            Choice(title=m, value=m, description=f"current: {pc.model}" if m == pc.model else None)
            for m in models
        ]
        chosen_model = await select(
            f"Select a model for {info.name if info and info.name else chosen_key}:",
            model_choices,
            default=pc.model if pc.model in models else models[0],
        )
        if chosen_model is None:
            return

        self._switch_to(chosen_key, chosen_model)

    def _switch_to(self, key: str, model: str):
        """Apply provider/model switch: update config, save, rebuild agent, preserve messages."""
        self._save_session()
        old_messages = self.agent.messages[:]

        self.config.provider = key
        pc = self.config.providers[key]
        pc.model = model
        # Resolve extra_body from the catalog map for the chosen model.
        info = self.config.provider_info.get(key)
        if info and info.extra_body_map:
            pc.extra_body = info.extra_body_map.get(model) or None

        self.config.save()
        self.agent = self._build_agent()
        self.agent.messages.extend(old_messages)
        self.agent.system_prompt = self._build_prompt()

        label = info.name if info and info.name else key
        self.display.info(f"Switched to {label} / {pc.model}")

    # ── /connect — provider management ────────────────────

    @staticmethod
    def _mask_key(k: str) -> str:
        if len(k) >= 4:
            return "•" * (len(k) - 4) + k[-4:]
        return "••••"

    async def _connect(self):
        """Top-level /connect menu — pick a provider to edit, or add new."""
        info_map = self.config.provider_info
        choices = []
        for key, pc in self.config.providers.items():
            info = info_map.get(key)
            title = info.name if info and info.name else key
            preview = ", ".join(info.models) if info and info.models else pc.model
            choices.append(Choice(title=title, value=key, description=preview))

        choices.append(Choice(title="+ Add new provider", value="__add__"))
        choices.append(Choice(title="Back", value="__back__"))

        chosen = await select("Manage providers:", choices, default=self.config.provider)
        if chosen is None or chosen == "__back__":
            return
        if chosen == "__add__":
            await self._connect_add()
            return
        await self._connect_edit(chosen)

    async def _connect_edit(self, key: str):
        """Edit submenu for one provider — loop until Back."""
        pc = self.config.providers[key]
        info = self.config.provider_info.get(key) or ProviderInfo()
        # Ensure info exists in config
        if key not in self.config.provider_info:
            self.config.provider_info[key] = info

        dirty = False

        while True:
            name_display = info.name or key
            models_display = ", ".join(info.models) if info.models else pc.model
            key_masked = self._mask_key(pc.api_key)

            choices = [
                Choice(title=f"Edit display name:  {name_display}", value="name"),
                Choice(title=f"Edit API key:       {key_masked}", value="apikey"),
                Choice(title=f"Edit base URL:      {pc.base_url or ''}", value="baseurl"),
                Choice(title=f"Edit models:        {models_display}", value="models"),
                Choice(title="Edit per-model extra_body", value="extra_body"),
                Choice(title="Delete provider", value="delete"),
                Choice(title="Back", value="back"),
            ]

            chosen = await select(f"Provider [{key}]:", choices)
            if chosen is None or chosen == "back":
                self._connect_apply(dirty)
                return

            if chosen == "name":
                result = await text_input("Display name:", default=info.name)
                if result is not None:
                    info.name = result
                    dirty = True

            elif chosen == "apikey":
                result = await text_input("API key:", default=pc.api_key)
                if result is not None:
                    pc.api_key = result
                    dirty = True

            elif chosen == "baseurl":
                result = await text_input("Base URL:", default=pc.base_url or "")
                if result is not None:
                    pc.base_url = result or None
                    dirty = True

            elif chosen == "models":
                default_str = ", ".join(info.models) if info.models else pc.model
                result = await text_input("Models (comma-separated):", default=default_str)
                if result is not None:
                    new_models = [m.strip() for m in result.split(",") if m.strip()]
                    if new_models:
                        old_active = pc.model
                        info.models = new_models
                        # If active model disappeared, reset to first
                        if old_active not in new_models:
                            pc.model = new_models[0]
                            self.display.warn(
                                f"Active model '{old_active}' removed, reset to '{new_models[0]}'"
                            )
                        dirty = True
                    else:
                        self.display.warn("Models list cannot be empty.")

            elif chosen == "extra_body":
                await self._connect_extra_body(key, info)
                dirty = True

            elif chosen == "delete":
                if key == self.config.provider:
                    self.display.warn(
                        f"Cannot delete active provider '{key}'. "
                        "Use /model to switch first."
                    )
                    continue
                if await confirm(f"Delete provider '{key}'?"):
                    del self.config.providers[key]
                    if key in self.config.provider_info:
                        del self.config.provider_info[key]
                    self.config.save()
                    self.display.info(f"Provider '{key}' deleted.")
                    return

    async def _connect_extra_body(self, key: str, info: ProviderInfo):
        """Sub-menu: pick a model, then edit its extra_body JSON."""
        if not info.models:
            self.display.warn("No models configured for this provider.")
            return

        # Pick which model to edit
        model_choices = [Choice(title=m, value=m) for m in info.models]
        model_choices.append(Choice(title="Back", value="__back__"))
        chosen_model = await select("Select model to edit extra_body:", model_choices)
        if chosen_model is None or chosen_model == "__back__":
            return

        # Show current value
        current_map = info.extra_body_map or {}
        current_val = current_map.get(chosen_model)
        default_str = json.dumps(current_val, ensure_ascii=False) if current_val else ""

        result = await text_input(
            f"extra_body for {chosen_model} (JSON or blank to clear):",
            default=default_str,
        )
        if result is None:
            return

        result = result.strip()
        if not result:
            # Clear this model's entry
            if info.extra_body_map and chosen_model in info.extra_body_map:
                del info.extra_body_map[chosen_model]
                if not info.extra_body_map:
                    info.extra_body_map = None
            return

        # Validate JSON
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError as e:
            self.display.error(f"Invalid JSON: {e}")
            return

        if info.extra_body_map is None:
            info.extra_body_map = {}
        info.extra_body_map[chosen_model] = parsed

    async def _connect_add(self):
        """Add a new provider — sequential prompts, abort on any cancel."""
        # 1. Provider key
        def _validate_key(s: str) -> bool | str:
            if not s.strip():
                return "Key cannot be empty"
            if s.strip() in self.config.providers:
                return f"Provider '{s.strip()}' already exists"
            return True

        key = await text_input("Provider key (e.g. 'openai'):", validate=_validate_key)
        if key is None:
            return
        key = key.strip()

        # 2. Display name
        name = await text_input("Display name (optional):")
        if name is None:
            return

        # 3. Base URL
        base_url = await text_input("Base URL (optional):")
        if base_url is None:
            return

        # 4. API key
        api_key = await text_input("API key:")
        if api_key is None:
            return
        if not api_key.strip():
            self.display.warn("API key cannot be empty. Aborted.")
            return

        # 5. Models
        def _validate_models(s: str) -> bool | str:
            if not [m.strip() for m in s.split(",") if m.strip()]:
                return "At least one model required"
            return True

        models_str = await text_input("Models (comma-separated):", validate=_validate_models)
        if models_str is None:
            return
        models = [m.strip() for m in models_str.split(",") if m.strip()]

        # Insert into config
        self.config.providers[key] = ProviderConfig(
            api_key=api_key.strip(),
            model=models[0],
            base_url=base_url.strip() or None,
        )
        self.config.provider_info[key] = ProviderInfo(
            name=name.strip() if name else "",
            models=models,
        )
        self._connect_apply(True)
        self.display.info(f"Provider '{key}' added.")

    def _connect_apply(self, dirty: bool):
        """Save config + rebuild agent if dirty."""
        if not dirty:
            return
        self._save_session()
        old_messages = self.agent.messages[:]
        self.config.save()
        self.agent = self._build_agent()
        self.agent.messages.extend(old_messages)
        self.agent.system_prompt = self._build_prompt()
        self.display.info("Config saved.")

    async def _dispatch(self, text: str):
        """Route slash commands. Returns "quit", "handled", or None."""
        low = text.lower()
        if low in ("exit", "quit", "/quit", "/exit"):
            return "quit"
        if low == "/export":
            self._export()
            return "handled"
        if low == "/clear":
            self._clear()
            return "handled"
        if low == "/model":
            await self._model()
            return "handled"
        if low == "/connect":
            await self._connect()
            return "handled"
        if low.startswith("/resume"):
            arg = text[len("/resume"):].strip()
            await self._resume(arg)
            return "handled"
        if low.startswith("/"):
            self.display.warn(f"Unknown command: {text.split()[0]}")
            return "handled"
        return None

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self):
        try:
            while True:
                try:
                    user_input = await self.display.prompt()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not user_input:
                    continue

                cmd = await self._dispatch(user_input)
                if cmd == "quit":
                    break
                if cmd == "handled":
                    continue

                self.display.user_message(user_input)

                task = asyncio.ensure_future(self.agent.chat(user_input))

                def _on_sigint(signum, frame):
                    if not task.done():
                        task.cancel()

                original_handler = signal.signal(signal.SIGINT, _on_sigint)
                try:
                    async with self.display.spinner("Thinking"):
                        result = await task
                except asyncio.CancelledError:
                    self.display.warn("\nResponse interrupted.\n")
                    continue
                finally:
                    signal.signal(signal.SIGINT, original_handler)

                if result:
                    self.display.response(result)

                self._session_mgr.mark_dirty()
        finally:
            self._save_session()

    # ── Entry point ────────────────────────────────────────

    def run(self):
        """Sync entry point for the CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self._session_mgr.save_if_dirty(
                self.agent.messages,
                model=self.config.model,
                provider=self.config.provider,
            )
