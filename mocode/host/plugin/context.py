"""BuildContext / HostContext — the shared blackboard between the host and its
plugins, split by lifecycle stage.

A plugin sees the context in two stages, and the stages are *types*:
``build()`` receives a :class:`BuildContext` — the contribution targets, the
configuration and the paths — and nothing about the agent, because the agent
does not exist until every plugin has contributed. ``prepare()``, ``close()``
and call time receive a :class:`HostContext`: the same object, grown by
assembly with its ``agent`` attached. What used to be a ``RuntimeError``
("emit during build") is now a fact a type checker can see before anything
runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...core.events import PluginMessage
from ...core.tool import ToolRegistry
from ..command import CommandRegistry
from ..config import Config

if TYPE_CHECKING:
    from ...core.agent import AgentLoop
    from ...core.channel import Subscription
    from ...core.events import Event
    from ...core.hook import AgentHook
    from ...core.prompt import Section
    from ...core.provider import ModelSpec
    from ..runtime import ProviderFactory


class _StampingToolRegistry(ToolRegistry):
    """A registry that attributes every registration to whoever is registering.

    ``source`` is a fact about the path, not a claim by the tool. The host
    sets the current source around each plugin's ``build()`` / ``prepare()``,
    and every tool registered in that window is stamped with it — a plugin
    cannot claim a builtin's identity by saying so, because the loader backs
    the stamp. Outside that window a registration belongs to the host itself.
    """

    def __init__(self, ctx: "BuildContext"):
        super().__init__()
        self._ctx = ctx

    def register(self, tool, *, replace: bool = False) -> "ToolRegistry":
        tool.source = self._ctx._current_source or "host"
        return super().register(tool, replace=replace)


@dataclass
class BuildContext:
    """What ``build()`` receives: contribution targets plus configuration.

    One context belongs to one conversation: ``cwd`` is the project that
    conversation works in, and ``tools`` / ``commands`` / ``hooks`` /
    ``prompt_sections`` are its contributions. There is no agent here — the
    agent is assembled after every plugin has contributed — so a plugin that
    needs call-time abilities (an event stream, a sub-agent) creates its own
    objects in ``build()`` and reaches the loop through the
    :class:`HostContext` this same object becomes, or through the
    ``ToolCallContext`` a tool receives.

    There is no display here on purpose. A plugin that has something to say
    publishes an event (:meth:`HostContext.emit`); whatever is watching reads
    the stream, and the host never learns what shape it has.
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
    #: Tools contributed for this conversation. The registry the context
    #: builds stamps each registration with its source — who is registering
    #: (the current plugin's channel-prefixed name, or the host) — so
    #: attribution is a fact of the path. Passing a registry in keeps it
    #: verbatim: an embedder that manages its own tools answers for them.
    tools: ToolRegistry = None  # type: ignore[assignment]
    commands: CommandRegistry = None  # type: ignore[assignment]
    hooks: list[AgentHook] = field(default_factory=list)
    prompt_sections: list[Section] = field(default_factory=list)
    #: Every plugin's own state for this conversation, keyed by plugin name.
    #: Seeded from the session a resume opens, swapped by ``load_session``,
    #: written back on save — the host owns the persistence, each plugin owns
    #: its slot's contents (:meth:`plugin_state`).
    plugin_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tools is None:
            self.tools = _StampingToolRegistry(self)
        if self.commands is None:
            self.commands = CommandRegistry()
        #: The source the current plugin's registrations are stamped with —
        #: set by :class:`~mocode.host.plugin.host.PluginHost` around each
        #: plugin's build()/prepare(), empty between them.
        self._current_source = ""

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

    def _with_agent(self, agent: "AgentLoop") -> "HostContext":
        """The host view of this very context: the same object, its agent
        attached.

        Called once, by :meth:`PluginHost.assemble
        <mocode.host.plugin.host.PluginHost.assemble>`, after every plugin has
        built. Identity is the point: a contributor that kept the context it
        was built with — the cache-protect watcher, a tool closing over
        ``ctx`` — sees the agent appear on the object it already holds, so
        nothing saved during ``build()`` goes stale when assembly happens.
        """
        self.__class__ = HostContext
        self.agent = agent
        return self  # type: ignore[return-value]


@dataclass
class HostContext(BuildContext):
    """The same context once the loop exists — what ``prepare()``, ``close()``
    and call time receive.

    Reached by assembly (a :class:`BuildContext` grows into this, same
    object) or constructed directly around an agent that already exists. The
    agent is never ``None`` here; that was the whole point of the split.
    """

    agent: AgentLoop = field(kw_only=True)

    async def emit(self, event: "Event") -> None:
        """Publish an event on this conversation's stream.

        Works between turns as well as during one: a plugin with something to
        say does not need a run in flight and does not need to know who is
        watching. During a run the event is attributed to it — stamped with
        the turn's id unless the publisher claimed one — so the turn's readers
        see what plugins said while it ran; between turns it carries no run id
        and belongs to the conversation stream alone. Either way it is never
        folded into the run's live state: that folding happens where the run
        owns the event, in the loop's own publishing path.

        Legal in ``prepare()`` and ``close()`` and at call time. A tool that
        wants to report progress mid-call has a shorter path: its
        ``ToolCallContext`` carries its own ``emit``.
        """
        turn = self.agent.turn
        if turn is not None and not turn.done and not event.run_id:
            event.run_id = turn.id
        await self.agent.channel.publish(event)

    async def emit_message(
        self, kind: str, data: dict, *, block_id: str = ""
    ) -> None:
        """Say something as a :class:`~mocode.core.events.PluginMessage`.

        The convenience over ``emit`` for the common shape — a plugin entry
        with a kind and a payload, optionally updating one display block:
        messages that share *block_id* render as one block rather than one
        each. The payload is copied, so later edits to the caller's dict never
        rewrite what was published. The messages are persisted with the
        session — the newest 200 of them — and replayed on the stream when the
        session resumes, so a frontend that draws blocks rebuilds them.
        """
        await self.emit(PluginMessage(kind=kind, data=dict(data), block_id=block_id))

    async def seal_message(self, block_id: str) -> None:
        """Mark a message block as finished.

        After the seal, an update arriving for the same *block_id* is a late
        arrival: a frontend appends it as a follow-up rather than rewriting
        the sealed block (there is no path back into a block that was already
        committed to a terminal's scrollback).
        """
        await self.emit(PluginMessage(block_id=block_id, sealed=True))

    def subscribe(self, *, since: int | None = None) -> "Subscription":
        """Read this conversation's event stream, out-of-band.

        The division of labour with hooks: ``on_event`` is in-band — the loop
        waits for it, so it belongs to code that must *answer*; this is for
        code that only *watches*. A subscription never slows a run: it has a
        bounded backlog and says what it dropped. Close it in
        :meth:`Plugin.close <mocode.host.plugin.base.Plugin.close` when it
        should not outlive the conversation.
        """
        return self.agent.channel.subscribe(since=since)

    def spawn(
        self,
        *,
        system_prompt: str,
        tools: ToolRegistry | None = None,
        model: ModelSpec | None = None,
        visible: bool = True,
    ) -> AgentLoop:
        """A sub-agent on this conversation's setup — ``derive()`` with the
        plugin-facing defaults fixed:

        * events are **visible** by default (``visible=True`` shares this
          agent's channel, so every reader of the conversation sees the
          nested work; ``visible=False`` gives the child a private stream),
        * hooks are **not** inherited (a sub-agent should not inherit the
          host's display or policy hooks — pass nothing and it runs clean),
        * tools default to a live copy of this agent's registry.

        Call-time use: during ``build()`` there is no agent yet. A tool that
        spawns closes over this context and spawns per call.
        """
        return self.agent.derive(
            system_prompt=system_prompt,
            tools=tools if tools is not None else self.agent.tool_registry.select(),
            model=model,
            channel=self.agent.channel if visible else None,
        )
