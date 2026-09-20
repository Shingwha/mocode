"""Conversation — one conversation in progress.

This is the unit an application works with: one project, one model, one history,
one stream of events. A web backend holds a dictionary of them; a terminal holds
exactly one; an editor holds whatever the user has open. The host keeps no
registry of its own, because the identity a conversation has in an application
(a route, a tab, a socket) is the application's business.

It owns four things and delegates the rest:

* its **agent** (:class:`~mocode.core.agent.AgentLoop`) — the engine, and the
  channel every event travels on;
* its **context** (:class:`~mocode.host.plugin.context.HostContext`) — the tools,
  commands, hooks and prompt sections built for this conversation from the
  plugins loaded for its project;
* its **identity** — the session id, its project, and the provider/model it uses;
* its **lifecycle** — run a turn, watch it, save, resume, close.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from ..core.agent import AgentLoop
from ..core.channel import Subscription
from ..core.events import Event, Notice
from ..core.tool import ToolRegistry
from .events import ConversationChanged
from .plugin.context import HostContext
from .plugin.host import PluginHost
from .session import (
    Session,
    extract_title,
    new_session_id,
    timestamp,
)

if TYPE_CHECKING:
    from ..core.agent import Turn
    from ..core.provider import ModelSpec, Provider
    from ..core.state import RunState
    from .command import CommandRegistry
    from .runtime import MoCode


class Conversation:
    """One live conversation: project, model, history, stream.

    Everything that differs between two conversations in the same process lives
    here rather than on the runtime: which directory the tools work in, which
    provider the model calls go to, which plugins were built (and therefore which
    shell session, skill index and tool instances exist), and what has been said.
    """

    def __init__(
        self,
        *,
        runtime: "MoCode",
        cwd: Path,
        ctx: HostContext,
        agent: AgentLoop,
        host: PluginHost,
        provider_key: str,
        model_name: str,
        session_id: str,
        created_at: str,
    ):
        self.runtime = runtime
        self.cwd = cwd
        self.ctx = ctx
        self.agent = agent
        self.host = host
        self.provider_key = provider_key
        self.model_name = model_name
        #: Identity of the session this conversation will be saved as. Assigned
        #: when it is created and written to disk on the first save.
        self.id = session_id
        self.created_at = created_at
        self._saved_at = ""

    # ── Running ────────────────────────────────────────────

    async def prepare(self) -> None:
        """Materialize the request surface — prompt and tool interface — now.

        The first request of the first turn does this on its own, after the
        plugins' async preparation; this is the explicit entry for an
        application or a test that wants the surface before any turn. The
        surface a resumed session ran on comes back byte-identical here.
        """
        await self.host.materialize()

    def run(self, prompt: str | None = None) -> "Turn":
        """Begin a turn. Raises if one is already running in this conversation.

        The turn belongs to the conversation: a reader that disconnects does not
        stop it, and another reader can join with :meth:`subscribe`.
        """
        return self.agent.start(prompt)

    def stream(self, prompt: str | None = None) -> AsyncIterator[Event]:
        """Run one turn and yield its events, scoped to this caller.

        Stop reading and the turn stops with you. Use :meth:`run` when the run
        should outlive whoever asked for it.
        """
        return self.agent.stream(prompt)

    async def chat(self, prompt: str | None = None) -> str:
        """Run one turn and return the final answer."""
        return await self.agent.chat(prompt)

    def subscribe(self, *, since: int | None = None) -> Subscription:
        """Read this conversation's stream — every turn, every notification.

        ``since`` replays what the channel still remembers after that ``seq``,
        which is how a reader that was away catches up; ``Subscription.dropped``
        and a gap in ``seq`` say when it could not.
        """
        return self.agent.channel.subscribe(since=since)

    @property
    def busy(self) -> bool:
        """Whether a turn is running right now."""
        return self.agent.busy

    def cancel(self) -> None:
        """Stop the running turn, if any."""
        turn = self.agent.turn
        if turn is not None:
            turn.cancel()

    # ── What it is ─────────────────────────────────────────

    @property
    def messages(self) -> list[dict]:
        """The conversation in OpenAI message format."""
        return self.agent.messages

    @property
    def state(self) -> "RunState":
        """Live snapshot of the current or last turn."""
        return self.agent.state

    @property
    def tools(self) -> ToolRegistry:
        return self.ctx.tools

    @property
    def commands(self) -> "CommandRegistry":
        return self.ctx.commands

    @property
    def model(self) -> "ModelSpec | None":
        return self.ctx.model

    @property
    def provider(self) -> "Provider":
        return self.agent.provider

    def set_model(self, key: str, model: str) -> None:
        """Point this conversation at another provider/model.

        This conversation only — no other conversation changes, and nothing is
        written to config.json. Persisting a default is a deliberate act
        (``runtime.set_default_model``), not a side effect of switching.
        """
        provider = self.runtime.provider_for(key, model)
        spec = self.runtime.config.model_spec(key, model)
        self.agent.provider = provider
        self.agent.model = spec
        self.ctx.model = spec
        self.provider_key = key
        self.model_name = model

    # ── Lifecycle ──────────────────────────────────────────

    def save(self, title: str | None = None) -> Session | None:
        """Write this conversation to the session store.

        Nothing to save (an empty history) means nothing written: opening a
        conversation does not litter the store.
        """
        if not self.agent.messages:
            return None
        session = self._as_session(
            updated_at=timestamp(),
            title=title if title is not None else extract_title(self.agent.messages),
        )
        self.runtime.store.save(str(self.cwd), session)
        self._saved_at = session.updated_at
        return session

    def session(self) -> Session:
        """This conversation as a session record, without writing it."""
        return self._as_session(
            updated_at=self._saved_at or self.created_at,
            title=extract_title(self.agent.messages),
        )

    async def new_session(self, messages: list[dict] | None = None) -> str:
        """Begin a new session in the same project, optionally seeded."""
        if self.busy:
            raise RuntimeError(
                "cannot start a new session while a turn is running — cancel it first"
            )
        self.save()
        self.id = new_session_id()
        self.created_at = timestamp()
        self.adopt(messages or [])
        await self.changed()
        return self.id

    async def load_session(self, session: Session) -> None:
        """Continue a stored session: its identity, its history, its model.

        The system prompt stays the one the session ran with — the provider's
        prefix cache for the old turns survives the resume. What has changed
        since the model was last told arrives as a notice appended to the
        history, not as a rewritten prompt.

        The model comes back with the conversation when the config still knows
        the provider it was using; otherwise the current one stays, and a caller
        that cares can compare ``session.provider`` with ``provider_key``.
        """
        if self.busy:
            raise RuntimeError("cannot resume while a turn is running — cancel it first")
        self.save()
        self.id = session.id
        self.created_at = session.created_at
        if session.provider and session.model:
            if self.runtime.config.providers.get(session.provider) is not None:
                self.set_model(session.provider, session.model)
        self.adopt(session.messages)
        self.reinstate(session)
        # The plugins' per-conversation state travels with the session: the
        # baselines they diff against become that session's, before anything
        # is announced.
        self.ctx.plugin_states = dict(session.plugin_state)
        await self.changed()

    def rebuild_prompt(self) -> None:
        """Re-render and re-freeze the system prompt — the explicit escape hatch.

        A resume keeps the frozen prompt and tells the model what changed as a
        notice; this replaces the prompt outright, accepting the cache loss.
        A pinned tool interface is re-pinned alongside it, and every plugin's
        per-conversation state is cleared: the model has just been re-told
        everything, so there is nothing left to announce.
        """
        self.host.rebuild()

    def list_sessions(self) -> list[Session]:
        """Sessions recorded for this project, newest first."""
        return self.runtime.store.list(str(self.cwd))

    async def aclose(self, *, save: bool = True) -> None:
        """End the conversation and wait for the ending: the full lifecycle.

        The turn's terminal event reaches every reader before the channel
        closes, and plugins are released only after the run has stopped using
        them. Prefer this over :meth:`close` wherever an event loop is running.
        """
        turn = self.agent.turn
        self.cancel()
        if turn is not None and not turn.done:
            try:
                await turn.wait()
            except BaseException:
                pass  # the turn ended badly; closing continues regardless
        self._teardown(save=save)

    def close(self, *, save: bool = True) -> None:
        """End the conversation without waiting: the emergency path.

        A running turn is *asked* to stop, not waited for — this stays callable
        from a `finally` — so the ending it reports arrives after this returns,
        possibly onto an already-closed channel. Use :meth:`aclose` instead
        wherever you can await.
        """
        self.cancel()
        self._teardown(save=save)

    def _teardown(self, *, save: bool) -> None:
        if save:
            self.save()
        self.host.close()
        self.agent.close()
        self.agent.channel.close(reason=f"conversation {self.id} closed")

    # ── Talking to whoever is watching ─────────────────────

    async def notify(self, text: str, *, level: str = "info") -> None:
        """Say something on this conversation's stream.

        ``level`` is ``info`` / ``warn`` / ``error``; a frontend decides how to
        show it, and one written later still can, because a ``Notice`` carries
        its own text.
        """
        await self.agent.channel.publish(Notice(message=text, level=level))

    async def changed(self) -> None:
        """Announce that the history was replaced — readers should redraw.

        The surface is materialized first: a notice is diffed against the
        prompt, and diffing against a placeholder would announce the world.
        """
        await self.host.materialize()
        await self.agent.channel.publish(ConversationChanged())

    # ── Internals ──────────────────────────────────────────

    def _as_session(self, *, updated_at: str, title: str) -> Session:
        """The same fields both ``save()`` and ``session()`` report."""
        return Session(
            id=self.id,
            created_at=self.created_at,
            updated_at=updated_at,
            workdir=str(self.cwd),
            messages=list(self.agent.messages),
            title=title,
            model=self.model_name,
            provider=self.provider_key,
            system_prompt=self.agent.system_prompt,
            tool_schemas=self.ctx.tools.all_schemas(),
            plugin_state=self.ctx.plugin_states,
        )

    def adopt(self, messages: list[dict]) -> None:
        """Replace the history in place, with no stale turn state behind it.

        The prompt is untouched — freezing it and noticing drift is
        :meth:`load_session`'s business.
        """
        self.agent.reset()
        self.agent.messages.extend(messages)

    def reinstate(self, session: Session) -> None:
        """Put back the session's frozen prompt and tool interface.

        Both are reinstated byte-identical, so a resume's request prefix is
        the one the old turns ran on and the provider's cache survives. A
        session recorded before either was frozen carries none of it, and the
        conversation materializes freshly at its first request instead. What
        the two now disagree with the live world is announced — see the
        cache-protect plugin.
        """
        self.host.reinstate(session)

    def __repr__(self) -> str:
        return f"<Conversation {self.id} {self.cwd}>"
