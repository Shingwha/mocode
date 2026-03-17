"""Gateway Manager - 管理所有渠道和用户会话"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from mocode import EventBus, EventType, MocodeClient

from .base import BaseChannel
from .config import GatewayConfig
from .telegram import TelegramChannel

# 命令注册表
COMMANDS = {
    "start": "Start conversation",
    "help": "Show commands",
    "clear": "Clear history",
    "model": "View/switch model",
    "provider": "View/switch provider",
    "status": "Show status",
    "cancel": "Cancel operation",
}


@dataclass
class UserSession:
    """用户会话"""

    user_id: str
    channel: str
    client: MocodeClient
    event_bus: EventBus


class GatewayManager:
    """Gateway 管理器

    管理:
    - 所有渠道实例 (Telegram, 飞书, 钉钉等)
    - 用户会话 (每个用户独立的 MocodeClient)
    - 消息路由和权限响应
    """

    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig.load()
        self.channels: dict[str, BaseChannel] = {}
        self.sessions: dict[str, UserSession] = {}
        self._running = False

    def get_commands(self) -> dict[str, str]:
        """返回命令注册表供 Channel 使用"""
        return COMMANDS

    def _format_help(self) -> str:
        """格式化帮助信息"""
        lines = [f"/{cmd} - {desc}" for cmd, desc in COMMANDS.items()]
        return "\n".join(lines)

    def _format_models(self, session: UserSession) -> str:
        """格式化模型列表"""
        lines = []
        config = session.client.config

        for prov_key, prov_config in config.providers.items():
            is_current = prov_key == config.current.provider
            marker = " *" if is_current else ""
            lines.append(f"**{prov_config.name}**{marker}")

            for model in prov_config.models:
                is_current_model = is_current and model == config.current.model
                check = " *" if is_current_model else ""
                lines.append(f"- {model}{check}")

        return "\n".join(lines)

    def _find_model_in_providers(
        self, session: UserSession, model_name: str
    ) -> tuple[str | None, str | None]:
        """在所有供应商中查找模型

        Returns:
            (provider_key, model_name) 如果找到，否则 (None, None)
        """
        config = session.client.config
        found_provider = None

        for prov_key, prov_config in config.providers.items():
            if model_name in prov_config.models:
                if found_provider is None:
                    found_provider = prov_key
                else:
                    # 在多个供应商中找到
                    return None, None

        return found_provider, model_name if found_provider else None

    def _format_providers(self, session: UserSession) -> str:
        """格式化供应商列表"""
        lines = []
        config = session.client.config

        for prov_key, prov_config in config.providers.items():
            is_current = prov_key == config.current.provider
            check = " *" if is_current else ""
            lines.append(f"{prov_key} - {prov_config.name}{check}")

        return "\n".join(lines)

    async def start(self) -> None:
        """启动所有渠道"""
        # 初始化 Telegram 渠道
        tg_config = self.config.get_telegram_config()
        if tg_config.enabled:
            channel = TelegramChannel(tg_config)
            channel_name = "telegram"
            # 设置命令注册表
            channel.set_command_registry(self.get_commands())
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
        except (KeyboardInterrupt, asyncio.CancelledError):
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
                await self.channels[channel].send_message(user_id, f"Error: {e}")

    def _get_or_create_session(self, channel: str, user_id: str) -> UserSession:
        """获取或创建用户会话"""
        session_key = f"{channel}:{user_id}"

        if session_key not in self.sessions:
            # 创建独立的事件总线
            event_bus = EventBus()

            # 创建 MocodeClient
            client = MocodeClient(
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
                ),
            )
            event_bus.on(
                EventType.TOOL_START,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_tool_start(ch, uid, e)
                ),
            )
            event_bus.on(
                EventType.INTERRUPTED,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_interrupted(ch, uid, e)
                ),
            )
            # 订阅 MESSAGE_ADDED - 记录用户输入
            event_bus.on(
                EventType.MESSAGE_ADDED,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_message_added(ch, uid, e)
                ),
            )
            # 订阅 TOOL_COMPLETE - 记录工具结果
            event_bus.on(
                EventType.TOOL_COMPLETE,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_tool_complete(ch, uid, e)
                ),
            )
            # 订阅 ERROR - 记录错误
            event_bus.on(
                EventType.ERROR,
                lambda e, ch=channel, uid=user_id: asyncio.create_task(
                    self._on_error(ch, uid, e)
                ),
            )

            print(f"[Gateway] New session: {session_key}")

        return self.sessions[session_key]

    async def _handle_command(
        self, session: UserSession, text: str, channel: str
    ) -> None:
        """处理命令"""
        parts = text.split()
        cmd = parts[0].lower().lstrip("/")  # 移除 "/" 前缀
        args = parts[1:] if len(parts) > 1 else []

        ch = self.channels[channel]

        if cmd == "start":
            await ch.send_message(session.user_id, "mocode Bot ready.")

        elif cmd == "help":
            await ch.send_message(session.user_id, self._format_help())

        elif cmd == "clear":
            session.client.clear_history()
            await ch.send_message(session.user_id, "History cleared.")

        elif cmd == "model":
            if args:
                model_name = args[0]
                # 跨供应商搜索模型
                provider_key, found_model = self._find_model_in_providers(
                    session, model_name
                )

                if provider_key and found_model:
                    session.client.set_model(found_model, provider_key)
                    session.client.config.save()  # 持久化配置
                    prov_name = session.client.config.providers[provider_key].name
                    await ch.send_message(
                        session.user_id,
                        f"Switched to **{found_model}** ({prov_name})",
                    )
                else:
                    # 尝试在当前供应商中查找
                    config = session.client.config
                    current_models = config.models
                    if model_name in current_models:
                        session.client.set_model(model_name)
                        session.client.config.save()  # 持久化配置
                        await ch.send_message(
                            session.user_id, f"Switched to **{model_name}**"
                        )
                    else:
                        await ch.send_message(
                            session.user_id,
                            f"Model `{model_name}` not found.\n\n{self._format_models(session)}",
                        )
            else:
                await ch.send_message(session.user_id, self._format_models(session))

        elif cmd == "provider":
            if args:
                provider_key = args[0].lower()
                config = session.client.config

                if provider_key in config.providers:
                    prov_config = config.providers[provider_key]
                    # 切换供应商，使用第一个模型
                    first_model = (
                        prov_config.models[0] if prov_config.models else "default"
                    )
                    session.client.set_model(first_model, provider_key)
                    session.client.config.save()  # 持久化配置
                    await ch.send_message(
                        session.user_id,
                        f"Switched to **{prov_config.name}** ({first_model})",
                    )
                else:
                    await ch.send_message(
                        session.user_id,
                        f"Provider `{provider_key}` not found.\n\n{self._format_providers(session)}",
                    )
            else:
                await ch.send_message(session.user_id, self._format_providers(session))

        elif cmd == "status":
            await ch.send_message(
                session.user_id,
                f"User: `{session.user_id}`\n"
                f"Channel: `{channel}`\n"
                f"Model: `{session.client.current_model}`\n"
                f"Provider: `{session.client.current_provider}`\n"
                f"Messages: {len(session.client.agent.messages)}",
            )

        elif cmd == "cancel":
            session.client.interrupt()
            await ch.send_message(session.user_id, "Cancelled.")

        else:
            # 未知命令，发送给 Agent
            await session.client.chat(text)

    async def _on_text_complete(self, channel: str, user_id: str, event) -> None:
        """处理文本完成事件"""
        content = event.data
        preview = content[:100] + "..." if len(content) > 100 else content
        print(f"[{channel}:{user_id}] <<< {preview}")
        if channel in self.channels:
            await self.channels[channel].send_message(user_id, event.data)

    async def _on_tool_start(self, channel: str, user_id: str, event) -> None:
        """处理工具开始事件"""
        tool_name = event.data.get("name", "unknown")
        tool_args = event.data.get("args", {})
        args_preview = json.dumps(tool_args, ensure_ascii=False)
        if len(args_preview) > 100:
            args_preview = args_preview[:100] + "..."
        print(f"[{channel}:{user_id}] Tool: {tool_name}({args_preview})")

        if channel in self.channels:
            # 发送工具执行通知
            await self.channels[channel].send_message(
                user_id,
                f"`{tool_name}`({args_preview})",
            )

    async def _on_interrupted(self, channel: str, user_id: str, event) -> None:
        """处理中断事件"""
        print(f"[{channel}:{user_id}] Interrupted")
        if channel in self.channels:
            await self.channels[channel].send_message(user_id, "Cancelled.")

    async def _on_message_added(self, channel: str, user_id: str, event) -> None:
        """用户消息添加 - 记录到控制台"""
        content = event.data.get("content", "")
        preview = content[:100] + "..." if len(content) > 100 else content
        print(f"[{channel}:{user_id}] >>> {preview}")

    async def _on_tool_complete(self, channel: str, user_id: str, event) -> None:
        """工具完成 - 记录结果到控制台"""
        result = self._preview_result(event.data.get("result", ""))
        print(f"[{channel}:{user_id}] Tool result: {result}")

    async def _on_error(self, channel: str, user_id: str, event) -> None:
        """错误处理 - 记录到控制台"""
        print(f"[{channel}:{user_id}] Error: {event.data}")

    def _preview_result(self, result: str) -> str:
        """生成结果预览"""
        lines = result.split("\n")
        preview = lines[0][:60]
        if len(lines) > 1:
            preview += f" ... +{len(lines) - 1} lines"
        elif len(lines[0]) > 60:
            preview += "..."
        return preview


async def run_gateway() -> None:
    """运行 Gateway"""
    manager = GatewayManager()
    await manager.run()
