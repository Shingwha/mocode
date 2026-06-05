"""CLI application — CLIApp class owns config, display, input, spinner, agent, commands."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from ..config import Config
from ..session import FileSessionStore, Session, SessionManager
from ...core import Agent
from ...core.agent import AgentConfig
from ...core.skill import SkillManager
from ...core.tool import ToolRegistry
from ...core.virtualfs import VirtualFS
from ...skills import AmesimTunerSkill, SimulinkTunerSkill, WorkflowSkill
from ..workflow import WorkflowRegistry
from ..workflow.cli import make_registry as _make_workflow_registry
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
from .commands.prompts import register_prompt_commands


class CLIApp:
    """Interactive CLI application — composable entry point for MoCode."""

    def __init__(
        self,
        config: Config | None = None,
        display: Display | None = None,
        home: Path | None = None,
        interactive: bool = True,
    ):
        self.home = home or Path.home() / ".mocode"
        self.interactive = interactive
        self.active_plan_path: str | None = None
        _fix_console()

        self.config = config or Config.load()
        if self.config is None:
            return  # caller checks and handles

        self.commands = CommandRegistry()
        register_prompt_commands(self.commands)
        if self.interactive:
            from .commands.quit import QuitCommand
            from .commands.help import HelpCommand
            from .commands.export import ExportCommand
            from .commands.clear import ClearCommand
            from .commands.model import ModelCommand
            from .commands.resume import ResumeCommand
            from .commands.connect import ConnectCommand
            from .commands.copy import CopyCommand
            from .commands.workflow import WorkflowCommand
            from .commands.compact import CompactCommand

            for cmd in [
                QuitCommand(),
                HelpCommand(),
                ExportCommand(),
                ClearCommand(),
                ModelCommand(),
                ResumeCommand(),
                ConnectCommand(),
                WorkflowCommand(),
                CopyCommand(),
                CompactCommand(),
            ]:
                self.commands.register(cmd)

        if self.interactive:
            from .input import Input
            from .display import Display
            from .workflow_renderer import WorkflowRenderer

            _input = Input(self.commands, ps1="❯")
            self.display = display or Display(input_=_input)
            self.wf_renderer = WorkflowRenderer(self.display)
        else:
            self.display = display  # may be None in non-interactive
            self.wf_renderer = None  # type: ignore[assignment]

        self._workflow_registry = _make_workflow_registry()

        self.agent = self._build_agent()

        self._session_mgr: SessionManager | None = None
        if self.interactive:
            self._session_mgr = SessionManager(
                workdir=str(Path.cwd()),
                store=FileSessionStore(),
            )
            # Lazy create — session is only created on first actual save

    # ── Console setup ─────────────────────────────────────

    @property
    def session_mgr(self) -> SessionManager:
        if self._session_mgr is None:
            raise RuntimeError("session_mgr not available in non-interactive mode")
        return self._session_mgr

    @property
    def workflow_registry(self) -> WorkflowRegistry:
        return self._workflow_registry

    # ── Agent construction ─────────────────────────────────

    def _create_provider(self) -> OpenAIProvider:
        """Create an OpenAIProvider from the current config entry."""
        entry = self.config.current
        return OpenAIProvider(
            api_key=entry.api_key,
            model=self.config.active_model,
            base_url=entry.base_url,
            extra_body=self.config.extra_body,
        )

    def _build_agent(self):
        """Build the AgentLoop with tools, hooks, and prompt."""
        provider = self._create_provider()

        agent_config = AgentConfig(
            max_tokens=self.config.max_tokens,
            tool_result_limit=self.config.tool_result_limit,
            tool_timeout=self.config.tool_timeout,
        )

        self._vfs = VirtualFS()

        self._tools = ToolRegistry()
        for t in [
            ReadTool(vfs=self._vfs),
            WriteTool(),
            AppendTool(),
            EditTool(),
            GlobTool(vfs=self._vfs),
            GrepTool(vfs=self._vfs),
            BashTool(),
            FetchTool(),
        ]:
            self._tools.register(t)

        self._skill_mgr = SkillManager([self.home / "skills"], vfs=self._vfs)

        # Register built-in skills
        self._skill_mgr.register(WorkflowSkill())
        self._skill_mgr.register(AmesimTunerSkill())
        self._skill_mgr.register(SimulinkTunerSkill())

        self._tools.register(SkillTool(self._skill_mgr))

        prompt = self._build_prompt()

        goal_hook = GoalHook()

        hooks = []
        if self.interactive:
            from .hook import CLIDisplayHook

            hooks.append(CLIDisplayHook(self.display))
        hooks.append(goal_hook)

        agent = (
            Agent()
            .provider(provider)
            .prompt(prompt)
            .tools(self._tools)
            .hooks(hooks)
            .config(agent_config)
            .build()
        )

        # agent exists now — attach hooks/tools that need agent reference
        agent.hooks.add(CompactHook(agent))
        self._tools.register(CompactTool(agent, lambda: agent.messages))
        self._tools.register(
            SubAgentTool(agent, self._tools, tool_timeout=agent.config.tool_timeout)
        )
        self._tools.register(GoalTool(goal_hook))

        from ...tools.plan import PlanTool
        self._tools.register(PlanTool(self))

        # Register /skill:<name> commands for each discovered skill
        if self.interactive:
            from .commands.skill import make_skill_command

            for s in self._skill_mgr.all():
                self.commands.register(make_skill_command(s))

        # Register /plan:* commands
        if self.interactive:
            from .commands.plan import (
                _PlanHandler,
                PlanStartCommand,
                PlanStartCleanCommand,
                PlanStatusCommand,
                PlanClearCommand,
            )
            handler = _PlanHandler(self)
            self.commands.register(PlanStartCommand(handler))
            self.commands.register(PlanStartCleanCommand(handler))
            self.commands.register(PlanStatusCommand(handler))
            self.commands.register(PlanClearCommand(handler))

        return agent

    def _build_prompt(self) -> str:
        """Build system prompt — re-reads AGENTS.md each time."""
        return build_system_prompt(
            tools=self._tools,
            skill_manager=self._skill_mgr,
            vfs=self._vfs,
            cwd=str(Path.cwd()),
            home=str(self.home),
            config_path=str(self.home / "config.json"),
            skills_dir=str(self.home / "skills"),
            sessions_dir=str(self.home / "sessions"),
        )

    # ── Session lifecycle ─────────────────────────────────

    def _save_current_session(self) -> None:
        """Persist current agent messages to the active session."""
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
        self._session_mgr.switch_to(session)
        self.agent.messages.clear()
        self.agent.messages.extend(session.messages)
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()
        if session.messages:
            self.display.render_messages(session.messages)

    def resume_from_file(self, messages: list[dict]) -> None:
        """Load messages from an external file — creates a new session."""
        self._save_current_session()
        self.agent.messages.clear()
        self.agent.messages.extend(messages)
        if self._session_mgr is not None:
            self._session_mgr.clear()
            self._session_mgr.create()
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()
        if messages:
            self.display.render_messages(messages)

    def clear_conversation(self) -> None:
        """Save and clear the current conversation."""
        self._save_current_session()
        self.agent.messages.clear()
        if self._session_mgr is not None:
            self._session_mgr.clear()
            self._session_mgr.create()
        self.agent.system_prompt = self._build_prompt()
        self.display.clear_screen()

    def switch_provider(self, key: str, model: str):
        """Apply provider/model switch — swap provider in-place."""
        self._save_current_session()
        self.config.active_provider = key
        self.config.active_model = model
        self.config.save()

        self.agent.provider = self._create_provider()

        label = self.config.current.name or key
        self.display.info(f"Switched to {label} / {model}")

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Resolve input: run command if slash-prefixed, otherwise mark for chat."""
        parts = text.split(None, 1)
        cmd_text = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.commands.get(cmd_text)
        if cmd is not None:
            ctx = CommandContext(app=self, args=args, display=self.display)
            return await cmd.run(ctx)

        if text.startswith("/"):
            self.display.warn(f"Unknown command: {cmd_text}")
            return CommandResult.CONTINUE

        return CommandResult(kind="chat", prompt=text)

    # ── Chat helper ────────────────────────────────────────

    async def _run_chat(self, prompt: str):
        """Send prompt to agent with spinner, cancellation, and response display."""
        task = asyncio.ensure_future(self.agent.chat(prompt))

        def _on_sigint(signum, frame):
            if not task.done():
                task.cancel()

        original_handler = signal.signal(signal.SIGINT, _on_sigint)
        try:
            async with self.display.spinner():
                self.display.spinner_set("thinking", "Thinking")
                result = await task
        except asyncio.CancelledError:
            self.display.warn("\nResponse interrupted.\n")
            return
        finally:
            signal.signal(signal.SIGINT, original_handler)

        if result:
            self.display.response(result)

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
                if result.kind in ("prompt", "chat"):
                    self.display.user_message(user_input)
                    await self._run_chat(result.prompt)
                    self._save_current_session()
        finally:
            self._save_current_session()

    # ── Entry point ────────────────────────────────────────

    def run(self):
        """Sync entry point for the interactive CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self._save_current_session()

    def run_oneshot(self, prompt: str, stdin_text: str | None = None):
        """Non-interactive: run one query, print response, exit."""
        try:
            result = asyncio.run(self._oneshot(prompt, stdin_text))
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            sys.exit(1)
        if result:
            print(result)

    async def _oneshot(self, prompt: str, stdin_text: str | None):
        """Resolve slash commands, compose prompt, run agent."""
        result = await self._dispatch(prompt)
        if result.kind not in ("prompt", "chat"):
            return None
        full_prompt = _compose_prompt(result.prompt, stdin_text)
        return await self.agent.chat(full_prompt)


# ── Module-level helpers ────────────────────────────────────


def _compose_prompt(prompt: str, stdin_text: str | None) -> str:
    """Combine stdin context with the user's prompt."""
    if not stdin_text or not stdin_text.rstrip():
        return prompt
    return f"{stdin_text.rstrip()}\n\n---\n\n{prompt}"


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
