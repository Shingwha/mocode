"""Gateway Manager - 管理所有渠道和用户会话"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from nanocode import NanoCodeClient, EventBus, EventType

from .base import BaseChannel
from .config import GatewayConfig
from .telegram import TelegramChannel


@dataclass
class UserSession:
    """用户会话"""
    user_id: str
    channel: str
    client: NanoCodeClient
    event_bus: EventBus


class GatewayManager:
    """Gateway 管理器

    管理:
    - 所有渠道实例 (Telegram, 飞书, 钉钉等)
    - 用户会话 (每个用户独立的 NanoCodeClient)
    - 消息路由和权限响应
    """

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig.load()
        self.channels: dict[str, BaseChannel] = {}
        self.sessions: dict[str, UserSession] = {}
        self._running = False

    async def start(self) -> None:
        """启动所有渠道"""
        # 初始化 Telegram 渠道
        tg_config = self.config.get_telegram_config()
        if tg_config.enabled:
            channel = TelegramChannel(tg_config)
            channel_name = "telegram"
            # 使用 lambda 绑定 channel 名称
            channel.on_message(
                lambda uid, txt, ch=channel_name: asyncio.create_task(
                    self._handle_message(ch, uid, txt)
                )
            )
            self.channels[channel_name] = channel

        # 启动所有渠道
        for name, channel in self.channels.items():
            try:
                await channel.start()
                print(f"[Gateway] Channel '{name}' started")
            except Exception as e:
                print(f"[Gateway] Failed to start channel '{name}': {e}")

        self._running = True
        print(f"[Gateway] Manager started with {len(self.channels)} channel(s)")

    async def stop(self) -> None:
        """停止所有渠道"""
        self._running = False

        # 取消所有 pending 的权限请求
        for session in self.sessions.values():
            for future in session.permission_futures.values():
                if not future.done():
                    future.cancel()

        # 停止所有渠道
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                print(f"[Gateway] Channel '{name}' stopped")
            except Exception as e:
                print(f"[Gateway] Error stopping channel '{name}': {e}")

        self.channels.clear()
        print("[Gateway] Manager stopped")

    async def run(self) -> None:
        """运行 Gateway（阻塞）"""
        await self.start()

        try:
            # 保持运行
            while self._running:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n[Gateway] Shutting down...")
        finally:
            await self.stop()

    async def _handle_message(self, channel: str, user_id: str, text: str) -> None:
        """处理来自渠道的消息"""
        try:
            # 获取或创建用户会话
            session = self._get_or_create_session(channel, user_id)

            # 处理命令
            if text.startswith("/"):
                await self._handle_command(session, text, channel)
                return

            # 发送到 Agent
            await session.client.chat(text)

        except Exception as e:
            print(f"[Gateway] Error handling message: {e}")
            if channel in self.channels:
                await self.channels[channel].send_message(
                    user_id, f"❌ 处理消息时出错: {e}"
                )

    def _get_or_create_session(self, channel: str, user_id: str) -> UserSession:
        """获取或创建用户会话"""
        session_key = f"{channel}:{user_id}"

        if session_key not in self.sessions:
            # 创建独立的事件总线
            event_bus = EventBus()

            # 创建 NanoCodeClient
            client = NanoCodeClient(
                event_bus=event_bus,
            )

            # Gateway 模式：不设置权限匹配器，自动允许所有工具
            client.agent.permission_matcher = None

            # 创建会话
            session = UserSession(
                user_id=user_id,
                channel=channel,
                client=client,
                event_bus=event_bus,
            )
            self.sessions[session_key] = session

            # 订阅事件
            event_bus.on(
                EventType.TEXT_COMPLETE,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_text_complete(ch, uid, e)
                )
            )
            event_bus.on(
                EventType.TOOL_START,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_tool_start(ch, uid, e)
                )
            )

            print(f"[Gateway] New session: {session_key}")

        return self.sessions[session_key]

    async def _handle_command(self, session: UserSession, text: str, channel: str) -> None:
        """处理命令"""
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        ch = self.channels[channel]

        if cmd == "/clear":
            session.client.clear_history()
            await ch.send_message(session.user_id, "🗑️ 对话历史已清空")

        elif cmd == "/model":
            if args:
                model_name = args[0]
                session.client.set_model(model_name)
                await ch.send_message(
                    session.user_id, f"🔄 已切换到模型: {model_name}"
                )
            else:
                await ch.send_message(
                    session.user_id,
                    f"📋 当前模型: {session.client.current_model}\n"
                    f"供应商: {session.client.current_provider}",
                )

        elif cmd == "/status":
            await ch.send_message(
                session.user_id,
                f"📊 *状态*\n\n"
                f"用户: `{session.user_id}`\n"
                f"渠道: `{channel}`\n"
                f"模型: `{session.client.current_model}`\n"
                f"供应商: `{session.client.current_provider}`\n"
                f"消息数: {len(session.client.agent.messages)}",
            )

        elif cmd in ("/start", "/help"):
            # 这些已经在 Channel 层处理过了
            pass

        else:
            # 未知命令，发送给 Agent
            await session.client.chat(text)

    async def _on_text_complete(self, channel: str, user_id: str, event) -> None:
        """处理文本完成事件"""
        if channel in self.channels:
            await self.channels[channel].send_message(user_id, event.data)

    async def _on_tool_start(self, channel: str, user_id: str, event) -> None:
        """处理工具开始事件"""
        if channel in self.channels:
            tool_name = event.data.get("name", "unknown")
            tool_args = event.data.get("args", {})

            # 发送工具执行通知
            args_preview = json.dumps(tool_args, ensure_ascii=False)
            if len(args_preview) > 100:
                args_preview = args_preview[:100] + "..."

            await self.channels[channel].send_message(
                user_id,
                f"🔧 执行工具: `{tool_name}`\n参数: `{args_preview}`",
            )


async def run_gateway() -> None:
    """运行 Gateway"""
    manager = GatewayManager()
    await manager.run()
