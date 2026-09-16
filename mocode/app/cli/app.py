"""CLIApp — composition root: config, plugin host, agent, and the REPL.

The app itself knows no tools and no commands. It builds a HostContext, lets
PluginHost fill it, and then drives the resulting agent.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.agent import AgentConfig
from ...core.tool import ToolRegistry
from ..config import DEFAULT_CONFIG_PATH, Config
from ..plugin.context import HostContext
from ..plugin.host import PluginHost
from ..prompt import build_system_prompt
from ..session import Session, SessionManager, SessionStore
from .commands import (
    CONTINUE,
    EXIT,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from .spinner import Priority, Truncate

if TYPE_CHECKING:
    from ...core.provider import Provider
    from .display import Display


class CLIApp:
    """Interactive CLI — composable entry point for MoCode."""

    def __init__(
        self,
        config: Config | None = None,
        display: "Display | None" = None,
        home: Path | None = None,
        interactive: bool = True,
    ):
        self.home = home or Path.home() / ".mocode"
        self.cwd = Path.cwd()
        self.interactive = interactive
        _fix_console()

        self.config = config or Config.load()
        if self.config is None:
            return  # caller checks and reports

        self.commands = CommandRegistry()
        self.display: Display | None = None
        if interactive:
            from .display import Display
            from .input import Input
            from .theme import Theme

            self.theme = Theme()
            self.display = display or Display(
                input_=Input(self.commands, ps1="❯"),
                styles=self.theme.display,
                palette=self.theme.palette,
                spinner_styles=self.theme.spinner,
                spinner_palette=self.theme.palette,
            )

        self._session_mgr: SessionManager | None = (
            SessionManager(workdir=str(self.cwd), store=SessionStore())
            if interactive
            else None
        )

        self.ctx = HostContext(
            home=self.home,
            cwd=self.cwd,
            config=self.config,
            interactive=interactive,
            display=self.display,
            model=self.config.model_spec(),
            tools=ToolRegistry(),
            commands=self.commands,
        )
        self.host = PluginHost(self.ctx)
        self.agent = self.host.run(
            provider=self._create_provider(), config=self._agent_config()
        )
    # ── Composition ────────────────────────────────────────

    def _create_provider(self) -> Provider:
        from ...providers.openai import OpenAIProvider  # lazy — avoids openai SDK at startup

        entry = self.config.current
        if entry is None:
            raise ValueError(
                f"Provider {self.config.active_provider!r} is not defined in "
                f"{DEFAULT_CONFIG_PATH} — add it there, or point active_provider at "
                "an existing one."
            )
        return OpenAIProvider(
            api_key=self.config.api_key,
            model=self.config.active_model,
            base_url=entry.base_url,
            extra_body=self.config.extra_body,
        )

    def _agent_config(self) -> AgentConfig:
        """Loop policy from config; model facts travel separately as a ModelSpec."""
        return AgentConfig(
            tool_timeout=self.config.agent.tool_timeout,
            max_iterations=self.config.agent.max_iterations,
        )

    def _rebuild_prompt(self) -> None:
        """Re-read AGENTS.md; tools/skills/sections are live references."""
        self.agent.system_prompt = build_system_prompt(self.ctx)

    @property
    def session_mgr(self) -> SessionManager:
        if self._session_mgr is None:
            raise RuntimeError("session_mgr not available in non-interactive mode")
        return self._session_mgr

    # ── Session lifecycle ──────────────────────────────────

    def _save_current_session(self) -> None:
        if self._session_mgr is None or not self.agent.messages:
            return
        self._session_mgr.save(
            self.agent.messages,
            model=self.config.active_model,
            provider=self.config.active_provider,
        )

    def resume_session(self, session: Session) -> None:
        """Resume an existing session — preserves session identity."""
        self._save_current_session()
        self.session_mgr.switch_to(session)
        self._load_messages(session.messages)

    def resume_from_file(self, messages: list[dict]) -> None:
        """Load messages from an external file — starts a new session."""
        self._save_current_session()
        self.session_mgr.clear()
        self.session_mgr.create()
        self._load_messages(messages)

    def clear_conversation(self) -> None:
        """Save and clear the current conversation."""
        self._save_current_session()
        self.agent.messages.clear()
        if self._session_mgr is not None:
            self._session_mgr.clear()
            self._session_mgr.create()
        self._rebuild_prompt()
        if self.display:
            self.display.clear_session()
            self.display.clear_screen()

    def switch_provider(self, key: str, model: str) -> None:
        """Apply a provider/model switch by swapping the provider in place."""
        self._save_current_session()
        self.config.active_provider = key
        self.config.active_model = model
        self.config.save()

        self.agent.provider = self._create_provider()
        self.ctx.model = self.config.model_spec()
        self.agent.model = self.ctx.model

        if self.display:
            label = self.config.current.name or key
            self.display.info(f"Switched to {label} / {model}")

    def _load_messages(self, messages: list[dict]) -> None:
        self.agent.messages.clear()
        self.agent.messages.extend(messages)
        self._rebuild_prompt()
        if self.display:
            self.display.clear_session()
            self.display.clear_screen()
            if messages:
                self.display.render_messages(messages, self.ctx.tools)

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Resolve input: run a command if slash-prefixed, else send it to the agent."""
        parts = text.split(None, 1)
        cmd_text = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.commands.get(cmd_text)
        if cmd is not None:
            ctx = CommandContext(app=self, args=args, display=self.display)
            return await cmd.handler(ctx)

        if text.startswith("/"):
            self._suggest_command(cmd_text)
            return CONTINUE

        return CommandResult(Kind.PROMPT, text)

    def _suggest_command(self, cmd_text: str) -> None:
        if self.display is None:
            return
        import difflib

        names = [c.name for c in self.commands.all()]
        matches = difflib.get_close_matches(cmd_text, names, n=1, cutoff=0.6)
        if matches:
            self.display.warn(f"Unknown command: {cmd_text} — did you mean {matches[0]}?")
        else:
            self.display.warn(f"Unknown command: {cmd_text}")

    # ── Chat ───────────────────────────────────────────────

    async def _run_chat(self, prompt: str) -> None:
        """Send a prompt to the agent with spinner and cancellation."""
        task = asyncio.ensure_future(self.agent.chat(prompt))

        def _on_sigint(signum, frame):
            if not task.done():
                task.cancel()

        original_handler = signal.signal(signal.SIGINT, _on_sigint)
        try:
            async with self.display.spinner():
                self.display.spinner_set(
                    "thinking", "Thinking", priority=Priority.NORMAL, truncate=Truncate.TAIL
                )
                result = await task
        except asyncio.CancelledError:
            self.display.warn("\nResponse interrupted.\n")
            return
        finally:
            signal.signal(signal.SIGINT, original_handler)

        if result:
            self.display.response(result)

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self) -> None:
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
                if result.kind is Kind.EXIT:
                    break
                if result.kind is Kind.PROMPT:
                    self.display.user_message(user_input)
                    await self._run_chat(result.prompt)
                    self._save_current_session()
        finally:
            self._save_current_session()

    def run(self) -> None:
        """Sync entry point for the interactive CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self._save_current_session()

    # ── Oneshot ────────────────────────────────────────────

    def run_oneshot(self, prompt: str, stdin_text: str | None = None) -> None:
        """Non-interactive: run one query, print the answer, exit."""
        try:
            result = asyncio.run(self._oneshot(prompt, stdin_text))
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            sys.exit(1)
        if result:
            print(result)

    async def _oneshot(self, prompt: str, stdin_text: str | None):
        result = await self._dispatch(prompt)
        if result.kind is not Kind.PROMPT or not result.prompt:
            return None
        return await self.agent.chat(_compose_prompt(result.prompt, stdin_text))


def _compose_prompt(prompt: str, stdin_text: str | None) -> str:
    """Combine stdin context with the user's prompt."""
    if not stdin_text or not stdin_text.rstrip():
        return prompt
    return f"{stdin_text.rstrip()}\n\n---\n\n{prompt}"


def _fix_console() -> None:
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
