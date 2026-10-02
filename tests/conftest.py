"""Shared fixtures: one config shape, one runtime, one way to read a terminal.

Nothing here touches the real ``~/.mocode`` — every runtime's home lives inside
the test's ``tmp_path``. Factories that need a directory are fixtures
(``make_mc``, ``wired``, ``plugin_host``); the rest are plain helpers imported
from this module (``from .conftest import write_plugin``).
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import pytest

from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.events import Notice
from mocode.core.hook import HookRunner
from mocode.core.tool import Tool, ToolRegistry
from mocode.host.command import Command, CommandContext, CommandRegistry, CommandResult
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.plugin.context import BuildContext
from mocode.host.plugin.host import PluginHost, load_plugins
from mocode.host.plugin.loader import HOST_NAMESPACE
from mocode.host.runtime import MoCode
from mocode.testing import MockProvider, say

if TYPE_CHECKING:
    from mocode.core.hook import AgentHook
    from mocode.host.conversation import Conversation

#: Every escape a terminal draw can emit — colour, cursor moves, clears.
ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """What a terminal draw looks like as plain text."""
    return ESCAPES.sub("", text)


def make_config() -> Config:
    """A config with two providers and no reachable endpoint.

    Tests replace the conversation's provider with a double, so the values here
    only have to be well-formed — and never real.
    """
    return Config(
        provider="test",
        model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models=[ModelEntry(id="test-model"), ModelEntry(id="other-model")],
            ),
            "second": ProviderEntry(
                name="Second",
                api_key="sk-other",
                base_url="http://localhost",
                models=[ModelEntry(id="second-model")],
            ),
        },
    )


@pytest.fixture
def make_mc(tmp_path: Path):
    """A runtime factory, for tests that need a config of their own.

    *overrides* are keyword arguments passed straight to :class:`MoCode` (its
    ``freeze_interface`` decision, say); everything else is the shape every
    test shares: a home inside ``tmp_path`` and no third-party plugin dirs.
    """

    def _make(
        config: Config | None = None,
        *,
        plugin_dirs: list[Path] | None = None,
        **overrides,
    ) -> MoCode:
        return MoCode(
            config=config if config is not None else make_config(),
            home=tmp_path / "home",
            plugin_dirs=plugin_dirs if plugin_dirs is not None else [],
            **overrides,
        )

    return _make


@pytest.fixture
def mc(make_mc) -> MoCode:
    """A runtime whose sessions and plugin loading live inside the test."""
    return make_mc()


# ── a conversation on a scripted model ──────────────────────


def script(*entries) -> list:
    """Script entries the way a test writes them.

    A string is plain text (``say``), anything else — a :class:`Response` or
    an exception to raise — passes through untouched.
    """
    return [say(entry) if isinstance(entry, str) else entry for entry in entries]


def wire(conversation: "Conversation", *entries, chunk_size: int = 0) -> MockProvider:
    """Swap a MockProvider into an existing conversation — a CLIApp's too."""
    provider = MockProvider(script(*entries), chunk_size=chunk_size)
    conversation.agent.provider = provider
    return provider


@pytest.fixture
def wired(mc: MoCode, tmp_path: Path):
    """A conversation with a MockProvider swapped in — (conversation, provider).

    Entries are script items (:func:`script`); ``cwd`` names the project the
    conversation works in, and ``mc=`` swaps in a runtime of the test's own
    (an unpinned one, say).
    """
    default_runtime = mc

    def _wired(*entries, cwd: Path | None = None, mc: MoCode | None = None):
        target = mc if mc is not None else default_runtime
        conversation = target.new_conversation(
            cwd=cwd if cwd is not None else tmp_path
        )
        return conversation, wire(conversation, *entries)

    return _wired


def project(tmp_path: Path, name: str) -> Path:
    """A project directory inside the test's temporary tree."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── a plugin directory ──────────────────────────────────────


def write_plugin(
    root: Path,
    name: str,
    code: str = "",
    *,
    manifest: dict | None = None,
    raw_manifest: str | None = None,
    skills: list[str] | None = None,
    package: dict[str, str] | None = None,
    cli: str = "",
    cli_package: dict[str, str] | None = None,
) -> Path:
    """Create ``root/<name>/`` in the Agent Plugins layout.

    *code* writes the single-file entry ``mocode/plugin.py``; *package* writes
    the package entry ``mocode/plugin/<file>`` instead — the multi-file form.
    *cli* / *cli_package* write the terminal's own namespace, so one directory
    can carry both surfaces. *skills* adds ``skills/<name>/SKILL.md`` stubs.
    """
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if raw_manifest is not None:
        (plugin_dir / "plugin.json").write_text(raw_manifest, encoding="utf-8")
    else:
        data = {"$schema": "https://agent-plugins.org/schemas/v1.json", "name": name}
        data.update(manifest or {})
        (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")

    if code:
        module = plugin_dir / HOST_NAMESPACE / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(code), encoding="utf-8")

    for filename, text in (package or {}).items():
        module = plugin_dir / HOST_NAMESPACE / "plugin" / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(text), encoding="utf-8")

    for skill in skills or []:
        skill_dir = plugin_dir / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: from a plugin\n---\n\nDo {skill}.",
            encoding="utf-8",
        )

    if cli:
        module = plugin_dir / "mocode.cli" / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(cli), encoding="utf-8")

    for filename, text in (cli_package or {}).items():
        module = plugin_dir / "mocode.cli" / "plugin" / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(text), encoding="utf-8")

    return plugin_dir


def skill_dir(base: Path, name: str, description: str, body: str = "") -> Path:
    """A standalone skill directory: SKILL.md with frontmatter, no plugin."""
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return path


# ── a host, without the runtime ─────────────────────────────


@pytest.fixture
def plugin_host(tmp_path: Path):
    """The runtime's own assembly sequence, without the runtime.

    ``BuildContext`` → ``PluginHost`` → ``build_all()`` → ``assemble()`` — the
    agent arrives with a MockProvider under it. ``load=`` discovers plugins
    from directories the way a project does; ``build=False`` / ``assemble=False``
    stop at that stage, for a test that is about the stage itself.
    """

    def _host(
        *,
        cwd: Path | None = None,
        config: Config | None = None,
        config_kwargs: dict | None = None,
        plugins: Sequence | None = None,
        sources: Sequence[str] | None = None,
        tools: ToolRegistry | None = None,
        hook=None,
        responses=None,
        load: list[Path] | None = None,
        build: bool = True,
        assemble: bool = True,
    ) -> PluginHost:
        ctx = BuildContext(
            home=tmp_path / "home",
            cwd=cwd if cwd is not None else tmp_path,
            config=config
            if config is not None
            else Config(provider="p", model="m", **(config_kwargs or {})),
        )
        if tools is not None:
            ctx.tools = tools
        if hook is not None:
            ctx.hooks.append(hook(ctx))
        loaded = (
            load_plugins(plugin_dirs=list(load), config=ctx.config)
            if load is not None
            else None
        )
        if loaded is not None:
            ctx.plugin_sources = list(loaded.sources)
        if plugins is not None:
            members = list(plugins)
        elif loaded is not None:
            members = loaded.plugins
        else:
            members = []
        host = PluginHost(
            ctx,
            members,
            sources=sources
            if sources is not None
            else (list(loaded.tool_sources) if loaded is not None else None),
        )
        if build:
            host.build_all()
        if assemble:
            host.assemble(
                provider=MockProvider(
                    responses if responses is not None else [say("done")]
                ),
                config=AgentConfig(),
            )
        return host

    return _host


# ── commands ────────────────────────────────────────────────


async def run_command(
    command: Command,
    conversation: "Conversation",
    *,
    args: str = "",
    commands: CommandRegistry | None = None,
) -> tuple[CommandResult, list]:
    """Run a command and collect what it published — what the user saw."""
    reader = conversation.subscribe()
    result = await command.handler(
        CommandContext(conversation=conversation, args=args, commands=commands)
    )
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return result, seen


def notices(events: list) -> list[Notice]:
    """The Notice events among *events* — what the frontend was told."""
    return [e for e in events if isinstance(e, Notice)]


def updates(conversation: "Conversation") -> list[dict]:
    """The cache-protect announcements in a conversation's history."""
    return [
        m
        for m in conversation.messages
        if m.get("role") == "user" and "[context update" in str(m.get("content", ""))
    ]


# ── bare agents ─────────────────────────────────────────────


def echo_tool(name: str = "echo", **kwargs) -> Tool:
    """A tool that answers ``echo:<value>`` — the usual stand-in."""
    return Tool(
        name=name,
        description="echo",
        schema={
            "type": "object",
            "properties": {"value": {"type": "string", "description": "v"}},
            "required": ["value"],
        },
        func=lambda args: f"echo:{args['value']}",
        **kwargs,
    )


def make_agent(
    *tools: Tool,
    hooks: "list[AgentHook] | HookRunner | None" = None,
    config: AgentConfig | None = None,
    provider: MockProvider | None = None,
    **kwargs,
) -> AgentLoop:
    """An AgentLoop with a registry built from *tools* and a scripted model.

    *hooks* may be the raw list a hook-producing factory returns; anything a
    caller states outright (``system_prompt``, ``model``) wins over the
    defaults.
    """
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    kwargs.setdefault("provider", provider or MockProvider())
    kwargs.setdefault("system_prompt", "sys")
    if "tools" not in kwargs:
        kwargs["tools"] = registry
    if "hooks" not in kwargs:
        kwargs["hooks"] = HookRunner(hooks) if hooks else HookRunner()
    if "config" not in kwargs:
        kwargs["config"] = config or AgentConfig()
    return AgentLoop(**kwargs)
