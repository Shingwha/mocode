"""CLI application — CLIApp class encapsulates all CLI logic."""

import asyncio
import json
import signal
import sys
from datetime import datetime
from pathlib import Path

from ..config import Config
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

        tools = ToolRegistry()
        for t in [
            ReadTool(), WriteTool(), AppendTool(), EditTool(),
            GlobTool(), GrepTool(), BashTool(), FetchTool(),
        ]:
            tools.register(t)

        ic = self.config.image
        if ic.enabled:
            tools.register(ImageTool(base_url=ic.base_url, api_key=ic.api_key, model=ic.model))

        skill_mgr = SkillManager([self.HOME / "skills"])
        tools.register(SkillTool(skill_mgr))

        # Read AGENTS.md: global (~/.mocode/AGENTS.md) + project (./AGENTS.md)
        # AGENTS.md holds agent-specific context that doesn't belong in README:
        # build steps, test commands, code conventions, security notes, etc.
        agents_parts = []
        for p in (self.HOME / "AGENTS.md", Path.cwd() / "AGENTS.md"):
            if p.exists():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    agents_parts.append(content)

        prompt = build_system_prompt(
            tools=tools, skill_manager=skill_mgr, cwd=str(Path.cwd()),
            agents="\n\n".join(agents_parts) if agents_parts else "",
            home=str(self.HOME),
            config_path=str(self.HOME / "config.json"),
            skills_dir=str(self.HOME / "skills"),
            sessions_dir=str(self.HOME / "sessions"),
        )

        compact_hook = CompactHook(provider)
        goal_hook = GoalHook()

        agent = (
            Agent()
            .provider(provider)
            .prompt(prompt)
            .tools(tools)
            .hooks([CLIDisplayHook(self.display), compact_hook, goal_hook])
            .build()
        )

        tools.register(CompactTool(provider, lambda: agent.messages))
        tools.register(
            SubAgentTool(lambda: agent.provider, tools, tool_timeout=agent.config.tool_timeout)
        )
        tools.register(GoalTool(goal_hook))

        return agent

    # ── Slash commands ─────────────────────────────────────

    def _export(self):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.cwd() / f"session_{ts}.json"
        path.write_text(
            json.dumps(self.agent.messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.display.info(f"Exported {len(self.agent.messages)} msgs → {path}")

    def _resume(self, arg: str):
        arg = arg.strip('"').strip("'")
        path = Path(arg).expanduser()
        if not path.exists():
            self.display.error(f"File not found: {path}")
            return
        try:
            messages = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.display.error(f"Failed to read JSON: {e}")
            return
        if not isinstance(messages, list):
            self.display.error("Invalid format: expected a JSON array of messages")
            return
        self.agent.messages.clear()
        self.agent.messages.extend(messages)
        self.display.clear_screen()
        self.display.render_messages(messages)
        user_count = sum(1 for m in messages if m.get("role") == "user")
        self.display.info(f"Resumed {len(messages)} messages ({user_count} user turns) from {path.name}")

    def _dispatch(self, text: str):
        """Route slash commands. Returns "quit", "handled", or None."""
        low = text.lower()
        if low in ("exit", "quit", "/quit", "/exit"):
            return "quit"
        if low == "/export":
            self._export()
            return "handled"
        if low.startswith("/resume"):
            arg = text[len("/resume"):].strip()
            if not arg:
                self.display.warn("Usage: /resume <path-to-session.json>")
            else:
                self._resume(arg)
            return "handled"
        if low.startswith("/"):
            self.display.warn(f"Unknown command: {text.split()[0]}")
            return "handled"
        return None

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self):
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

    # ── Entry point ────────────────────────────────────────

    def run(self):
        """Sync entry point for the CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            pass
