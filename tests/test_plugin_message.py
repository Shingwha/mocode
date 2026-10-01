"""PluginMessage and the emit_message / seal_message host API.

The generic message channel of the plugin system: a plugin says something as a
first-class entry on the conversation's stream, addressed at whatever is
watching — no custom event class per plugin, no renderer registration. These
tests hold the event's contract (serialization, summary, block addressing) and
the two HostContext conveniences that publish it, including the run-id
attribution a turn's readers rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mocode.core.agent import AgentConfig
from mocode.core.events import PluginMessage
from mocode.core.hook import AgentHook
from mocode.host.config import Config
from mocode.host.plugin.context import BuildContext
from mocode.host.plugin.host import PluginHost
from mocode.testing import MockProvider, say


def _host(
    tmp_path: Path, hook: "Callable[[BuildContext], AgentHook] | None" = None
) -> PluginHost:
    """A built and assembled host — its ctx is a HostContext with an agent.

    *hook* is a factory receiving the context (it exists before assembly, so a
    hook may close over it — the same shape the cache-protect watcher uses).
    """
    ctx = BuildContext(
        home=tmp_path / "home",
        cwd=tmp_path,
        config=Config(active_provider="p", active_model="m"),
    )
    if hook is not None:
        ctx.hooks.append(hook(ctx))
    host = PluginHost(ctx, [])
    host.build_all()
    host.assemble(provider=MockProvider([say("done")]), config=AgentConfig())
    return host


class TestPluginMessage:
    def test_the_fields_and_their_defaults(self):
        event = PluginMessage(kind="shell/background-done")
        assert (event.kind, event.data, event.block_id, event.sealed) == (
            "shell/background-done",
            {},
            "",
            False,
        )
        assert event.type == "plugin_message"

    def test_to_dict_is_plain_data_with_the_nested_payload(self):
        @dataclass
        class Inner:
            n: int = 1

        event = PluginMessage(
            kind="rag/index",
            data={"done": 12, "inner": Inner(), "tags": ["a", {"b": 2}]},
            block_id="rag-1",
        )
        data = event.to_dict()

        assert data["type"] == "plugin_message"
        assert data["kind"] == "rag/index"
        assert data["block_id"] == "rag-1"
        assert data["sealed"] is False
        assert data["data"] == {"done": 12, "inner": {"n": 1}, "tags": ["a", {"b": 2}]}

    def test_the_dict_round_trips_through_the_fields(self):
        """A transport rebuilds the event from ``to_dict`` and gets it back."""
        original = PluginMessage(
            kind="shell/background-done",
            data={"jobs": [{"id": "shell_1", "exit_code": 0}]},
            block_id="shell-bg-1",
        )
        flat = original.to_dict()
        rebuilt = PluginMessage(
            kind=flat["kind"],
            data=flat["data"],
            block_id=flat["block_id"],
            sealed=flat["sealed"],
        )
        assert rebuilt.to_dict() == flat

    def test_a_seal_serializes_with_the_block_id(self):
        data = PluginMessage(block_id="rag-1", sealed=True).to_dict()
        assert data["sealed"] is True and data["block_id"] == "rag-1" and data["kind"] == ""

    def test_summary_names_the_kind(self):
        assert PluginMessage(kind="rag/index", data={"done": 1}).summary() == (
            "plugin message: rag/index"
        )


class TestRunIdAttribution:
    """A PluginMessage published through ``HostContext.emit`` during a turn is
    attributed to it — the turn's readers see what plugins said while it ran."""

    @pytest.mark.asyncio
    async def test_a_message_during_a_turn_is_stamped_with_its_run_id(
        self, tmp_path: Path
    ):
        class Emit(AgentHook):
            def __init__(self, ctx):
                self._ctx = ctx

            async def before_iteration(self, ctx) -> None:
                await self._ctx.emit(
                    PluginMessage(kind="shell/background-done", data={"jobs": []})
                )

        host = _host(
            tmp_path,
            hook=lambda ctx: Emit(ctx),
        )

        assert await host.ctx.agent.chat("go") == "done"

        messages = [
            e for e in host.ctx.agent.channel.history() if isinstance(e, PluginMessage)
        ]
        assert len(messages) == 1
        assert messages[0].run_id == host.ctx.agent.turn.id
