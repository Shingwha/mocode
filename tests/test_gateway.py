"""Tests for mocode.app.gateway — Gateway."""

import asyncio

import pytest

from mocode.app.gateway import (
    AgentFactory,
    ChatContext,
    Gateway,
    PendingMedia,
    chat_session,
    register_gateway_tools,
)
from mocode.channels.types import InboundMessage, OutboundMessage
from mocode.core import AgentConfig, AgentLoop, HookRunner, ToolRegistry
from mocode.core.provider import Response


# ---- Mock provider ----


class FakeProvider:
    model = "test-model"

    def __init__(self, responses=None):
        self._responses = responses or ["hello"]
        self._call_count = 0

    async def call(self, messages, system, tools, max_tokens):
        if self._call_count < len(self._responses):
            content = self._responses[self._call_count]
        else:
            content = self._responses[-1]
        self._call_count += 1
        return Response(content=content, tool_calls=None, usage=None, finish_reason="stop")


def _make_agent(session_key: str) -> AgentLoop:
    provider = FakeProvider(["reply one", "reply two"])
    return AgentLoop(
        provider=provider,
        system_prompt="test",
        tools=ToolRegistry(),
        hooks=HookRunner(),
        config=AgentConfig(),
    )


class FakeChannel:
    """Minimal channel implementation for testing."""

    def __init__(self, name: str = "test"):
        self._name = name
        self.started = False
        self.stopped = False
        self.sent: list[OutboundMessage] = []

    @property
    def name(self) -> str:
        return self._name

    async def start(self, on_message) -> None:
        self.started = True
        self._on_message = on_message

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)

    async def stop(self) -> None:
        self.stopped = True


# ---- Tests ----


class TestGateway:
    def test_create(self):
        ch = FakeChannel()
        gw = Gateway(channels={"test": ch}, agent_factory=_make_agent)
        assert gw._channels == {"test": ch}

    def test_create_with_session_store(self, tmp_path):
        from mocode.app import FileSessionStore

        store = FileSessionStore(base_dir=tmp_path)
        ch = FakeChannel()
        gw = Gateway(
            channels={"test": ch},
            agent_factory=_make_agent,
            session_store=store,
        )
        assert gw._session_store is store

    def test_get_or_create_caches(self):
        gw = Gateway(channels={}, agent_factory=_make_agent)
        s1 = gw._get_or_create("test:user1")
        s2 = gw._get_or_create("test:user1")
        assert s1 is s2
        assert s1.agent is not None

    def test_get_or_create_different_keys(self):
        gw = Gateway(channels={}, agent_factory=_make_agent)
        s1 = gw._get_or_create("test:user1")
        s2 = gw._get_or_create("test:user2")
        assert s1 is not s2
        assert s1.agent is not s2.agent

    def test_lru_eviction(self):
        gw = Gateway(channels={}, agent_factory=_make_agent, max_users=2)
        gw._get_or_create("test:user1")
        gw._get_or_create("test:user2")
        gw._get_or_create("test:user3")  # should evict user1
        assert "test:user1" not in gw._sessions
        assert "test:user3" in gw._sessions

    @pytest.mark.asyncio
    async def test_on_channel_message_inbound(self):
        gw = Gateway(channels={}, agent_factory=_make_agent)
        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="u1", content="hi"
        )
        await gw._on_channel_message(msg)
        assert gw._bus.inbound.qsize() == 1
        got = await gw._bus.inbound.get()
        assert got.content == "hi"

    @pytest.mark.asyncio
    async def test_on_channel_message_duck_type(self):
        gw = Gateway(channels={}, agent_factory=_make_agent)

        class DuckMsg:
            channel = "test"
            sender_id = "u1"
            chat_id = "u1"
            content = "hello"
            media = []
            metadata = {}

        await gw._on_channel_message(DuckMsg())
        got = await gw._bus.inbound.get()
        assert got.content == "hello"

    @pytest.mark.asyncio
    async def test_dispatch_inbound(self):
        ch = FakeChannel()
        gw = Gateway(channels={"test": ch}, agent_factory=_make_agent)

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="u1", content="hi"
        )
        await gw._bus.inbound.put(msg)

        t_in = asyncio.create_task(gw._dispatch_inbound())
        t_out = asyncio.create_task(gw._dispatch_outbound())
        await asyncio.sleep(0.2)
        t_in.cancel()
        t_out.cancel()
        await asyncio.gather(t_in, t_out, return_exceptions=True)

        assert ch.sent
        assert ch.sent[0].content == "reply one"

    @pytest.mark.asyncio
    async def test_dispatch_inbound_with_media(self):
        ch = FakeChannel()
        gw = Gateway(channels={"test": ch}, agent_factory=_make_agent)

        msg = InboundMessage(
            channel="test",
            sender_id="u1",
            chat_id="u1",
            content="hi",
            media=["/tmp/test.png"],
        )
        await gw._bus.inbound.put(msg)

        t_in = asyncio.create_task(gw._dispatch_inbound())
        t_out = asyncio.create_task(gw._dispatch_outbound())
        await asyncio.sleep(0.2)
        t_in.cancel()
        t_out.cancel()
        await asyncio.gather(t_in, t_out, return_exceptions=True)

        assert ch.sent

    @pytest.mark.asyncio
    async def test_dispatch_inbound_error(self):
        def bad_factory(session_key):
            agent = _make_agent(session_key)
            agent.chat = _raise_error
            return agent

        async def _raise_error(text, images=None):
            raise RuntimeError("boom")

        ch = FakeChannel()
        gw = Gateway(channels={"test": ch}, agent_factory=bad_factory)

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="u1", content="hi"
        )
        await gw._bus.inbound.put(msg)

        t_in = asyncio.create_task(gw._dispatch_inbound())
        t_out = asyncio.create_task(gw._dispatch_outbound())
        await asyncio.sleep(0.2)
        t_in.cancel()
        t_out.cancel()
        await asyncio.gather(t_in, t_out, return_exceptions=True)

        assert ch.sent
        assert "[Error]" in ch.sent[0].content

    @pytest.mark.asyncio
    async def test_session_persistence(self, tmp_path):
        from mocode.app import FileSessionStore

        store = FileSessionStore(base_dir=tmp_path)
        gw = Gateway(
            channels={}, agent_factory=_make_agent, session_store=store
        )

        session = gw._get_or_create("test:user1")
        assert session.sessions is not None
        session.sessions.create(metadata={"gateway_session_key": "test:user1"})

        await session.agent.chat("hello")
        gw._save_session(session)

        sessions = session.sessions.list()
        assert len(sessions) >= 1

    @pytest.mark.asyncio
    async def test_session_resume(self, tmp_path):
        from mocode.app import FileSessionStore

        store = FileSessionStore(base_dir=tmp_path)

        # First gateway creates and saves a session
        gw1 = Gateway(channels={}, agent_factory=_make_agent, session_store=store)
        s1 = gw1._get_or_create("test:user1")
        s1.sessions.create(metadata={"gateway_session_key": "test:user1"})
        await s1.agent.chat("first message")
        gw1._save_session(s1)

        # Second gateway resumes the session
        gw2 = Gateway(channels={}, agent_factory=_make_agent, session_store=store)
        s2 = gw2._get_or_create("test:user1")
        assert len(s2.agent.messages) > 0


class TestChatSession:
    @pytest.mark.asyncio
    async def test_context_vars_set_and_reset(self):
        ctx = ChatContext(session_key="test:u1", channel="test", chat_id="u1")
        async with chat_session(ctx) as pending:
            assert pending is not None
            assert isinstance(pending, PendingMedia)
        # After exit, ContextVars are reset
        from mocode.app.gateway import _current_media, _current_chat_id, _current_channel

        assert _current_media.get() is None
        assert _current_chat_id.get() == ""
        assert _current_channel.get() == ""


class TestRegisterGatewayTools:
    def test_send_file_registered(self):
        registry = ToolRegistry()
        register_gateway_tools(registry)
        assert registry.get("send_file") is not None

    @pytest.mark.asyncio
    async def test_send_file_queues_path(self):
        registry = ToolRegistry()
        register_gateway_tools(registry)
        tool = registry.get("send_file")

        from mocode.app.gateway import _current_media

        pending = PendingMedia()
        token = _current_media.set(pending)
        try:
            result = tool.run({"path": "/tmp/test.png"})
            assert "queued" in result
            assert "/tmp/test.png" in pending.paths
        finally:
            _current_media.reset(token)

    def test_send_file_no_context(self):
        registry = ToolRegistry()
        register_gateway_tools(registry)
        tool = registry.get("send_file")

        from mocode.app.gateway import _current_media

        token = _current_media.set(None)
        try:
            result = tool.run({"path": "/tmp/test.png"})
            assert "Error" in result
        finally:
            _current_media.reset(token)


class TestIsImage:
    def test_image_extensions(self):
        assert Gateway._is_image("photo.png")
        assert Gateway._is_image("photo.jpg")
        assert Gateway._is_image("photo.jpeg")
        assert Gateway._is_image("photo.gif")
        assert Gateway._is_image("photo.webp")

    def test_non_image(self):
        assert not Gateway._is_image("doc.pdf")
        assert not Gateway._is_image("audio.mp3")
