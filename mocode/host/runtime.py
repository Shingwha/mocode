"""MoCode — the runtime an application drives.

It owns what belongs to the whole process: the config, the home directory, the
session store, and the plugin set each project loads. What it does *not* own is a
conversation — it opens them. That split is what lets one process hold as many
conversations as it likes, in as many projects, on as many models at once::

    from mocode import MoCode

    mc = MoCode()                                  # ~/.mocode/config.json
    conv = mc.new_conversation(cwd="/srv/proj-a")  # one conversation
    async for event in conv.chat("list the tests"):
        ...                                        # text, tool calls, usage

    conv.state.answer                              # live snapshot
    conv.save()                                    # → ~/.mocode/sessions/…

A conversation is addressable, concurrent and independent: this runtime keeps no
registry of live ones, because the identity a conversation has in an application
(a route, a tab, a socket) is that application's business. ``resume()`` is the
one way back in from a stored id.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from ..core.agent import AgentConfig
from ..core.tool import ToolRegistry
from .command import CommandRegistry
from .config import DEFAULT_CONFIG_PATH, Config
from .conversation import Conversation
from .plugin.context import HostContext
from .plugin.host import (
    LoadedPlugins,
    PluginHost,
    default_plugin_dirs,
    load_plugins,
)
from .session import Session, SessionStore, new_session_id, timestamp

if TYPE_CHECKING:
    from ..core.provider import Provider
    from .plugin.base import Plugin


class MoCode:
    """A configured MoCode runtime: config, plugins, sessions — and conversations.

    Cheap to construct and safe to hold several of: nothing here is global, so
    two runtimes (two users, two configs, two homes) never see each other.
    """

    def __init__(
        self,
        *,
        config: Config | None = None,
        home: Path | None = None,
        plugin_dirs: Sequence[Path] | None = None,
    ):
        self.home = Path(home) if home is not None else Path.home() / ".mocode"
        self.config = config if config is not None else Config.load()
        if self.config is None:
            raise ValueError(
                f"No usable config at {DEFAULT_CONFIG_PATH} — create it first."
            )
        self.store = SessionStore(base_dir=self.home / "sessions")
        self._plugin_dirs = list(plugin_dirs) if plugin_dirs is not None else None
        #: Loaded plugin sets, keyed by project. Loading is per working
        #: directory; building is per conversation.
        self._plugins: dict[str, LoadedPlugins] = {}

    # ── Conversations ──────────────────────────────────────

    def new_conversation(
        self,
        *,
        cwd: Path | str | None = None,
        provider: str | None = None,
        model: str | None = None,
        commands: CommandRegistry | None = None,
        session: Session | None = None,
    ) -> Conversation:
        """Open a conversation in *cwd*, on one provider/model.

        Defaults: the process working directory, and the provider/model the
        config marks active. Everything else — plugins, tools, the shell's
        working directory, the system prompt — follows from those two.
        """
        project = Path(cwd) if cwd is not None else Path.cwd()
        key = provider or (session.provider if session else "") or self.config.active_provider
        name = model or (session.model if session else "") or self.config.active_model

        commands = commands if commands is not None else CommandRegistry()
        loaded = self._loaded_for(project)
        ctx = HostContext(
            home=self.home,
            cwd=project,
            config=self.config,
            model=self.config.model_spec(key, name),
            tools=ToolRegistry(),
            commands=commands,
            plugin_sources=list(loaded.sources),
        )
        host = PluginHost(ctx, loaded.plugins)
        agent = host.run(
            provider=self.provider_for(key, name), config=self._agent_config()
        )
        conversation = Conversation(
            runtime=self,
            cwd=project,
            ctx=ctx,
            agent=agent,
            host=host,
            provider_key=key,
            model_name=name,
            session_id=session.id if session else new_session_id(),
            created_at=session.created_at if session else timestamp(),
        )
        if session is not None:
            conversation.adopt(session.messages)
        return conversation

    def resume(self, session_id: str) -> Conversation | None:
        """Open the stored session *session_id*, wherever its project was."""
        session = self.store.find(session_id)
        if session is None:
            return None
        return self.new_conversation(
            cwd=session.workdir, provider=session.provider, model=session.model,
            session=session,
        )

    # ── Plugins ────────────────────────────────────────────

    def plugins_for(self, cwd: Path) -> list[Plugin]:
        """The plugins a project loads — discovered and imported once.

        Same project, same set: a second conversation in the same directory
        reuses what the first one loaded. A plugin that needs per-conversation
        state creates it in ``build()``, which runs for every conversation.
        """
        return list(self._loaded_for(cwd).plugins)

    def plugin_sources_for(self, cwd: Path) -> list[Path]:
        """Directories the project's plugins live in.

        What a frontend reads to find its own namespace (``<source>/mocode.cli/``)
        and what a plugin reads to find the files it ships.
        """
        return list(self._loaded_for(cwd).sources)

    def _loaded_for(self, cwd: Path) -> LoadedPlugins:
        key = str(Path(cwd).resolve())
        if key not in self._plugins:
            dirs = self._plugin_dirs
            if dirs is None:
                dirs = default_plugin_dirs(Path(cwd), self.home)
            self._plugins[key] = load_plugins(plugin_dirs=dirs, config=self.config)
        return self._plugins[key]

    # ── Providers ──────────────────────────────────────────

    def provider_for(self, key: str, model: str) -> "Provider":
        """Build a provider for a ``(provider key, model)`` pair.

        Raises ``ValueError`` when the config does not know the provider — a
        model MoCode cannot reach is a configuration mistake, not a fallback.
        """
        from ..providers.openai import OpenAIProvider  # lazy — no SDK at startup

        entry = self.config.providers.get(key)
        if entry is None:
            raise ValueError(
                f"Provider {key!r} is not defined in {self.config.path} — add it "
                "there, or point active_provider at an existing one."
            )
        model_entry = entry.models.get(model)
        return OpenAIProvider(
            api_key=entry.api_key_for(key),
            model=model,
            base_url=entry.base_url,
            extra_body=model_entry.extra_body if model_entry else None,
        )

    def set_default_model(self, key: str, model: str) -> None:
        """Record the provider/model new conversations start from.

        The only place that writes the active selection to config.json: a
        conversation switching models is a decision about that conversation,
        while this is a decision about the file.
        """
        if key not in self.config.providers:
            raise ValueError(
                f"Provider {key!r} is not defined in {self.config.path}"
            )
        self.config.active_provider = key
        self.config.active_model = model
        self.config.save()

    # ── Internals ──────────────────────────────────────────

    def _agent_config(self) -> AgentConfig:
        """Loop policy from config; model facts travel separately as a ModelSpec."""
        return AgentConfig(
            tool_timeout=self.config.agent.tool_timeout,
            max_iterations=self.config.agent.max_iterations,
        )
