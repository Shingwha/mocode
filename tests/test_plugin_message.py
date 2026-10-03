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


from mocode.core.events import PluginMessage
from mocode.core.hook import AgentHook


class TestPluginMessage:
    def test_the_fields_their_defaults_and_their_serialization(self):
        """字段与缺省在场，type 判别值钉住；to_dict 是纯数据——嵌套的
        dataclass 与列表都摊平成 JSON 形状；封块就是把 kind 腾空、sealed
        立起来。"""
        event = PluginMessage(kind="shell/background-done")
        assert (event.kind, event.data, event.block_id, event.sealed) == (
            "shell/background-done",
            {},
            "",
            False,
        )
        assert event.type == "plugin_message"

        @dataclass
        class Inner:
            n: int = 1

        nested = PluginMessage(
            kind="rag/index",
            data={"done": 12, "inner": Inner(), "tags": ["a", {"b": 2}]},
            block_id="rag-1",
        )
        data = nested.to_dict()

        assert data["type"] == "plugin_message"
        assert data["kind"] == "rag/index"
        assert data["block_id"] == "rag-1"
        assert data["sealed"] is False
        assert data["data"] == {"done": 12, "inner": {"n": 1}, "tags": ["a", {"b": 2}]}

        # 封块的序列化形态
        sealed = PluginMessage(block_id="rag-1", sealed=True).to_dict()
        assert sealed["sealed"] is True and sealed["block_id"] == "rag-1" and sealed["kind"] == ""

        # A transport rebuilds the event from ``to_dict`` and gets it back.
        rebuilt = PluginMessage(
            kind=data["kind"],
            data=data["data"],
            block_id=data["block_id"],
            sealed=data["sealed"],
        )
        assert rebuilt.to_dict() == data

    def test_summary_names_the_kind(self):
        assert PluginMessage(kind="rag/index", data={"done": 1}).summary() == (
            "plugin message: rag/index"
        )


class TestRunIdAttribution:
    """A PluginMessage published through ``HostContext.emit`` during a turn is
    attributed to it — the turn's readers see what plugins said while it ran."""

    async def test_a_message_during_a_turn_is_stamped_with_its_run_id(
        self, plugin_host
    ):
        class Emit(AgentHook):
            def __init__(self, ctx):
                self._ctx = ctx

            async def before_iteration(self, ctx) -> None:
                await self._ctx.emit(
                    PluginMessage(kind="shell/background-done", data={"jobs": []})
                )

        host = plugin_host(hook=lambda ctx: Emit(ctx))

        assert await host.ctx.agent.chat("go") == "done"

        messages = [
            e for e in host.ctx.agent.channel.history() if isinstance(e, PluginMessage)
        ]
        assert len(messages) == 1
        assert messages[0].run_id == host.ctx.agent.turn.id


class TestEmitMessage:
    """The two HostContext conveniences — plain PluginMessage publishing."""

    async def test_publishes_a_plugin_message_between_turns(self, plugin_host):
        host = plugin_host()

        await host.ctx.emit_message(
            "rag/index", {"done": 12, "total": 40}, block_id="rag-1"
        )

        message = host.ctx.agent.channel.history()[-1]
        assert isinstance(message, PluginMessage)
        assert (message.kind, message.data, message.block_id, message.sealed) == (
            "rag/index",
            {"done": 12, "total": 40},
            "rag-1",
            False,
        )
        # Between turns the entry belongs to the conversation stream alone.
        assert message.run_id == ""

        # seal_message 落在同一条通道上：块号不变，sealed 立起来，kind 腾空
        await host.ctx.seal_message("rag-1")

        sealed = host.ctx.agent.channel.history()[-1]
        assert isinstance(sealed, PluginMessage)
        assert (sealed.block_id, sealed.sealed, sealed.kind) == ("rag-1", True, "")

    async def test_the_payload_is_copied_not_shared(self, plugin_host):
        host = plugin_host()

        payload = {"done": 1}
        await host.ctx.emit_message("rag/index", payload)
        payload["done"] = 99

        message = host.ctx.agent.channel.history()[-1]
        assert message.data == {"done": 1}

    def test_the_sdk_exports_it(self):
        import mocode.plugins as sdk

        assert sdk.PluginMessage is PluginMessage
