"""Gateway MessageBus tests"""

import pytest

from mocode.gateway.bus import InboundMessage, MessageBus, OutboundMessage


class TestInboundMessage:
    def test_session_key(self):
        msg = InboundMessage(
            channel="wechat",
            sender_id="user1",
            chat_id="chat1",
            content="hello",
        )
        assert msg.session_key == "wechat:chat1"

    def test_default_values(self):
        msg = InboundMessage(
            channel="c", sender_id="s", chat_id="ch", content="hi",
        )
        assert msg.media == []
        assert msg.metadata == {}


class TestOutboundMessage:
    def test_default_values(self):
        msg = OutboundMessage(channel="c", chat_id="ch", content="hi")
        assert msg.media == []
        assert msg.metadata == {}


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_inbound_pub_sub(self):
        bus = MessageBus()
        msg = InboundMessage(
            channel="wechat", sender_id="u1", chat_id="c1", content="test",
        )
        await bus.publish_inbound(msg)
        received = await bus.consume_inbound()
        assert received.content == "test"
        assert received.session_key == "wechat:c1"

    @pytest.mark.asyncio
    async def test_outbound_pub_sub(self):
        bus = MessageBus()
        msg = OutboundMessage(channel="wechat", chat_id="c1", content="reply")
        await bus.publish_outbound(msg)
        received = await bus.consume_outbound()
        assert received.content == "reply"

    @pytest.mark.asyncio
    async def test_ordering(self):
        bus = MessageBus()
        for i in range(5):
            await bus.publish_inbound(
                InboundMessage(
                    channel="ch", sender_id="s", chat_id="c", content=str(i),
                )
            )

        results = []
        for _ in range(5):
            msg = await bus.consume_inbound()
            results.append(msg.content)
        assert results == ["0", "1", "2", "3", "4"]
