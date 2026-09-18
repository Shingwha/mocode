"""MoCode — the headless runtime, for embedding MoCode in another application.

It reads config, loads plugins, assembles the agent and owns the session. It
prints nothing and knows nothing about a terminal: a turn is an event stream,
and whatever is watching decides how it looks. The interactive CLI is one such
watcher; an editor, a web backend or a script is another.

::

    from mocode import MoCode

    mc = MoCode()                       # ~/.mocode/config.json, cwd
    async for event in mc.chat("list the tests"):
        ...                             # react to text, tool calls, usage
    print(mc.state.answer)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from ..core.agent import AgentConfig, AgentLoop
from ..core.events import Event
from ..core.tool import ToolRegistry
from .command import CommandRegistry
from .config import DEFAULT_CONFIG_PATH, Config
from .frontend import Frontend
from .plugin.base import Plugin
from .plugin.context import HostContext
from .plugin.host import PluginHost
from .prompt import build_system_prompt
from .session import Session, SessionManager, SessionStore

if TYPE_CHECKING:
    from ..core.provider import ModelSpec, Provider
    from ..core.state import RunState


class MoCode:
    """A configured MoCode instance: plugins, agent, session, no UI.

    Pass ``display`` to attach a frontend; leave it ``None`` for a fully
    headless run. Everything else is identical either way — the same plugins
    load and the same tools are registered.
    """

    def __init__(
        self,
        *,
        config: Config | None = None,
        home: Path | None = None,
        cwd: Path | None = None,
        display: Frontend | None = None,
        commands: CommandRegistry | None = None,
        plugin_dirs: list[Path] | None = None,
        extra_plugins: list[Plugin] | None = None,
    ):
        self.home = home or Path.home() / ".mocode"
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.display = display

        self.config = config if config is not None else Config.load()
        if self.config is None:
            raise ValueError(
                f"No usable config at {DEFAULT_CONFIG_PATH} — create it first."
            )

        # A caller that needs the registry before construction (a terminal
        # completer does) passes its own; plugins fill whichever one this is.
        self.commands = commands if commands is not None else CommandRegistry()
        self.ctx = HostContext(
            home=self.home,
            cwd=self.cwd,
            config=self.config,
            display=display,
            model=self.config.model_spec(),
            tools=ToolRegistry(),
            commands=self.commands,
        )
        self.host = PluginHost(
            self.ctx, plugin_dirs=plugin_dirs, extra_plugins=extra_plugins
        )
        self.agent: AgentLoop = self.host.run(
            provider=self._create_provider(), config=self._agent_config()
        )
        self.sessions = SessionManager(workdir=str(self.cwd), store=SessionStore())

    # ── Running ────────────────────────────────────────────

    def chat(
        self, prompt: str, *, images: list[str] | None = None
    ) -> AsyncIterator[Event]:
        """Run one turn, yielding events as they happen.

        The caller owns the stream: stop iterating to abandon the turn, or
        cancel the consuming task to interrupt it mid-flight.
        """
        return self.agent.stream(prompt, images=images)

    @property
    def state(self) -> "RunState":
        """Live snapshot of the current or last turn."""
        return self.agent.state

    @property
    def messages(self) -> list[dict]:
        """The conversation in OpenAI message format."""
        return self.agent.messages

    @property
    def tools(self) -> ToolRegistry:
        return self.ctx.tools

    @property
    def model(self) -> "ModelSpec | None":
        return self.ctx.model

    @property
    def provider(self) -> "Provider":
        return self.agent.provider

    # ── Conversation lifecycle ─────────────────────────────

    def rebuild_prompt(self) -> None:
        """Re-read AGENTS.md; tools/skills/sections are live references."""
        self.agent.system_prompt = build_system_prompt(self.ctx)

    def replace_messages(self, messages: list[dict]) -> None:
        """Swap the whole conversation and rebuild the system prompt around it."""
        self.agent.messages.clear()
        self.agent.messages.extend(messages)
        self.rebuild_prompt()

    def save_session(self, title: str | None = None) -> None:
        """Persist the conversation. A no-op when there is nothing to save."""
        if not self.agent.messages:
            return
        self.sessions.save(
            self.agent.messages,
            model=self.config.active_model,
            provider=self.config.active_provider,
            title=title,
        )

    def use_session(self, session: Session) -> None:
        """Continue an existing session, preserving its identity."""
        self.save_session()
        self.sessions.switch_to(session)
        self.replace_messages(session.messages)

    def start_session(self, messages: list[dict] | None = None) -> None:
        """Begin a new session, optionally seeded with messages from elsewhere."""
        self.save_session()
        self.sessions.clear()
        self.sessions.create()
        self.replace_messages(messages or [])

    def switch_provider(self, key: str, model: str) -> None:
        """Apply a provider/model switch by swapping the provider in place."""
        self.save_session()
        self.config.active_provider = key
        self.config.active_model = model
        self.config.save()

        self.agent.provider = self._create_provider()
        self.ctx.model = self.config.model_spec()
        self.agent.model = self.ctx.model

    # ── Composition ────────────────────────────────────────

    def _create_provider(self) -> "Provider":
        from ..providers.openai import OpenAIProvider  # lazy — no SDK at startup

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
