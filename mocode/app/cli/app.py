"""CLI application — CLIApp class owns config, display, input, spinner, agent, commands."""

import asyncio
import signal
import sys
from pathlib import Path

from ..config import Config
from ..session import FileSessionStore, SessionManager
from ...core import Agent
from ...core.agent import AgentConfig
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
    ReadTool,
    SkillTool,
    SubAgentTool,
    WriteTool,
)
from .commands import CommandContext, CommandRegistry, CommandResult
from .commands.quit import QuitCommand
from .commands.help import HelpCommand
from .commands.export import ExportCommand
from .commands.clear import ClearCommand
from .commands.model import ModelCommand
from .commands.resume import ResumeCommand
from .commands.connect import ConnectCommand
from .display import Display
from .hook import CLIDisplayHook


class CLIApp:
    """Interactive CLI application — composable entry point for MoCode."""

    def __init__(
        self,
        config: Config | None = None,
        display: Display | None = None,
        home: Path | None = None,
    ):
        self.home = home or Path.home() / ".mocode"
        _fix_console()

        self.config = config or Config.load()
        if self.config is None:
            return  # caller checks and handles

        self.display = display or Display()
        self.commands = CommandRegistry()
        self._register_commands()
        self.display.set_commands(self.commands.all())

        self.agent = self._build_agent()
        self._session_mgr = SessionManager(
            workdir=str(Path.cwd()),
            store=FileSessionStore(),
        )
        self._session_mgr.create()

    # ── Console setup ─────────────────────────────────────

    @property
    def session_mgr(self) -> SessionManager:
        return self._session_mgr

    # ── Command registration ──────────────────────────────

    def _register_commands(self):
        for cmd in [
            QuitCommand(),
            HelpCommand(),
            ExportCommand(),
            ClearCommand(),
            ModelCommand(),
            ResumeCommand(),
            ConnectCommand(),
        ]:
            self.commands.register(cmd)

    # ── Agent construction ─────────────────────────────────

    def _build_agent(self):
        """Build the AgentLoop with tools, hooks, and prompt."""
        entry = self.config.current

        provider = OpenAIProvider(
            api_key=entry.api_key,
            model=self.config.active_model,
            base_url=entry.base_url,
            extra_body=self.config.extra_body,
        )

        agent_config = AgentConfig(
            max_tokens=self.config.max_tokens,
            tool_result_limit=self.config.tool_result_limit,
            tool_timeout=self.config.tool_timeout,
        )

        self._tools = ToolRegistry()
        for t in [
            ReadTool(), WriteTool(), AppendTool(), EditTool(),
            GlobTool(), GrepTool(), BashTool(), FetchTool(),
        ]:
            self._tools.register(t)

        self._skill_mgr = SkillManager([self.home / "skills"])
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
            .config(agent_config)
            .build()
        )

        self._tools.register(CompactTool(provider, lambda: agent.messages))
        self._tools.register(
            SubAgentTool(
                lambda: agent.provider, self._tools,
                tool_timeout=agent.config.tool_timeout,
            )
        )
        self._tools.register(GoalTool(goal_hook))

        return agent

    def _build_prompt(self) -> str:
        """Build system prompt — re-reads AGENTS.md each time."""
        return build_system_prompt(
            tools=self._tools,
            skill_manager=self._skill_mgr,
            cwd=str(Path.cwd()),
            home=str(self.home),
            config_path=str(self.home / "config.json"),
            skills_dir=str(self.home / "skills"),
            sessions_dir=str(self.home / "sessions"),
        )

    # ── Rebuild / message helpers ──────────────────────────

    def _save_session(self):
        """Persist current messages to session store. Skips empty sessions."""
        if not self.agent.messages:
            return
        self._session_mgr.save(
            self.agent.messages,
            model=self.config.active_model,
            provider=self.config.active_provider,
        )

    def rebuild_agent(self):
        """Save session, rebuild agent, restore messages. Single path for all rebuilds."""
        self._save_session()
        old_messages = self.agent.messages[:]
        self.config.save()
        self.agent = self._build_agent()
        self.agent.messages.extend(old_messages)
        self.agent.system_prompt = self._build_prompt()
        self.display.info("Config saved.")

    def replace_messages(self, messages: list[dict]):
        """Swap agent messages — used by /clear and /resume."""
        self._save_session()
        self.agent.messages.clear()
        self.agent.messages.extend(messages)
        self._session_mgr.clear()
        self._session_mgr.create()
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()
        if messages:
            self.display.render_messages(messages)

    def switch_to(self, key: str, model: str):
        """Apply provider/model switch."""
        self._save_session()
        old_messages = self.agent.messages[:]

        self.config.active_provider = key
        self.config.active_model = model

        self.config.save()
        self.agent = self._build_agent()
        self.agent.messages.extend(old_messages)
        self.agent.system_prompt = self._build_prompt()

        entry = self.config.providers[key]
        label = entry.name or key
        self.display.info(f"Switched to {label} / {model}")

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Route slash commands through the registry."""
        low = text.lower()
        parts = text.split(None, 1)
        cmd_text = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.commands.get(cmd_text)
        if cmd is not None:
            ctx = CommandContext(app=self, args=args, display=self.display)
            return await cmd.run(ctx)

        if low.startswith("/"):
            self.display.warn(f"Unknown command: {cmd_text}")
            return CommandResult.CONTINUE

        return CommandResult.CONTINUE  # not a command — fall through to chat

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

                result = await self._dispatch(user_input)
                if result == CommandResult.EXIT:
                    break
                # If dispatch handled it (CONTINUE) but text started with /,
                # skip chat. If not a command, fall through.
                low = user_input.lower()
                cmd = self.commands.get(low.split(None, 1)[0])
                if cmd is not None or low.startswith("/"):
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
                model=self.config.active_model,
                provider=self.config.active_provider,
            )


# ── Module-level helpers ────────────────────────────────────


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
