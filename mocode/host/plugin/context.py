"""HostContext — the shared blackboard between the host and its plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ...core.tool import ToolRegistry
from ..command import Command, CommandRegistry
from ..config import Config

if TYPE_CHECKING:
    from ...core.agent import AgentLoop
    from ...core.channel import Subscription
    from ...core.events import Event
    from ...core.hook import AgentHook
    from ...core.prompt import Section
    from ...core.provider import ModelSpec
    from ..runtime import ProviderFactory


@dataclass
class HostContext:
    """Everything a plugin may read, and everything it may contribute to.

    One context belongs to one conversation: ``cwd`` is the project that
    conversation works in, ``tools``/``commands``/``hooks``/``prompt_sections``
    are its contributions, and ``agent`` is its loop once assembled.

    Fields fall into three groups:

    - **Ready at construction**: paths, config, and the shared registries.
    - **Contribution targets**: ``tools`` / ``commands`` / ``hooks`` /
      ``prompt_sections`` — plugins write into these during ``build()``.
    - **Assigned later**: ``agent``, set by the host once the loop is built.

    There is no display here on purpose. A plugin that has something to say
    publishes an event (:meth:`emit`); whatever is watching reads the stream,
    and the host never learns what shape it has.
    """

    # ── Host state ──
    home: Path
    cwd: Path
    config: Config
    #: Facts about the model in use (name, context window, output cap). Known
    #: before assembly, so plugins may read it during build().
    model: ModelSpec | None = None
    #: Directories of the plugins loaded for this project. A plugin finds the
    #: files it ships through them — ``<source>/skills/`` is the standard's
    #: location for portable skills, and a frontend finds ``<source>/<its
    #: namespace>/`` the same way. The host never looks inside one.
    plugin_sources: list[Path] = field(default_factory=list)
    #: Provider registration, wired by the runtime to its own
    #: ``register_provider_type``. A plugin that ships a provider
    #: implementation calls this in ``build()`` and config entries with
    #: ``"type": <name>`` start resolving — for this conversation, because
    #: ``build()`` runs before the provider is built, and every later one on
    #: the same runtime. ``None`` when the context was built outside a
    #: runtime: there is nothing to register with.
    register_provider_type: Callable[[str, "ProviderFactory"], None] | None = None

    # ── Contribution targets ──
    tools: ToolRegistry = None  # type: ignore[assignment]
    commands: CommandRegistry = None  # type: ignore[assignment]
    hooks: list[AgentHook] = field(default_factory=list)
    prompt_sections: list[Section] = field(default_factory=list)
    #: Every plugin's own state for this conversation, keyed by plugin name.
    #: Seeded from the session a resume opens, swapped by ``load_session``,
    #: written back on save — the host owns the persistence, each plugin owns
    #: its slot's contents (:meth:`plugin_state`).
    plugin_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── Assigned after assembly ──
    agent: AgentLoop | None = None

    def __post_init__(self) -> None:
        if self.tools is None:
            self.tools = ToolRegistry()
        if self.commands is None:
            self.commands = CommandRegistry()

    def plugin_config(self, name: str) -> dict:
        """Settings for plugin *name* from the ``plugins`` section of config.json."""
        return self.config.plugins.get(name, {})

    def plugin_state(self, name: str) -> dict[str, Any]:
        """This plugin's own state for this conversation — created empty, and
        it survives: the host persists it with the session and hands it back
        on resume.

        Keyed by plugin name, so plugins never see each other's slots, and
        generic, so nothing here knows what any plugin keeps. It is available
        at ``build()`` (a resumed conversation arrives with the session's
        state) and at call time; the host swaps the whole mapping when a
        conversation loads a different session, and clears it on
        ``rebuild_prompt``, when the model is re-told everything.
        """
        return self.plugin_states.setdefault(name, {})

    def register(self, *commands: Command) -> None:
        """Register slash commands contributed by a plugin."""
        for command in commands:
            self.commands.register(command)

    async def emit(self, event: "Event") -> None:
        """Publish an event on this conversation's stream.

        Works between turns as well as during one: a plugin with something to
        say does not need a run in flight and does not need to know who is
        watching. Available at call time — during ``build()`` there is no agent
        to publish through yet.
        """
        if self.agent is None:
            raise RuntimeError(
                "ctx.emit() during build(): the agent is assembled after every "
                "plugin has contributed — emit at call time instead"
            )
        await self.agent.channel.publish(event)

    def subscribe(self, *, since: int | None = None) -> "Subscription":
        """Read this conversation's event stream, out-of-band.

        The division of labour with hooks: ``on_event`` is in-band — the loop
        waits for it, so it belongs to code that must *answer*; this is for
        code that only *watches*. A subscription never slows a run: it has a
        bounded backlog and says what it dropped. Close it in
        :meth:`Plugin.close <mocode.host.plugin.base.Plugin.close` when it
        should not outlive the conversation.

        Like :meth:`emit`, this works at call time — during ``build()`` there
        is no agent to subscribe to yet.
        """
        if self.agent is None:
            raise RuntimeError(
                "ctx.subscribe() during build(): the agent is assembled after "
                "every plugin has contributed — subscribe at call time instead"
            )
        return self.agent.channel.subscribe(since=since)
