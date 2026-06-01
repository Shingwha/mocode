"""CLI application — CLIApp class encapsulates all CLI logic."""

import asyncio
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

from ..config import Config
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

    def _resume(self, arg: str):
        """Resume a session by ID or numeric index."""
        sessions = self._session_mgr.list()

        if not arg:
            self.display.session_list(sessions, active_id=self._session_mgr.active_id)
            self.display.info("Usage: /resume <id-or-number>")
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

    def _sessions(self):
        """List sessions for current working directory."""
        sessions = self._session_mgr.list()
        self.display.session_list(sessions, active_id=self._session_mgr.active_id)

    def _dispatch(self, text: str):
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
        if low == "/sessions":
            self._sessions()
            return "handled"
        if low.startswith("/resume"):
            arg = text[len("/resume"):].strip()
            self._resume(arg)
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

                cmd = self._dispatch(user_input)
                if cmd == "quit":
                    break
                if cmd == "handled":
                    continue
                print()

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
