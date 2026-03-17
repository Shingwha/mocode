"""Telegram Channel 实现"""

import asyncio
from typing import Callable, Awaitable

from .base import BaseChannel
from .config import TelegramConfig


class TelegramChannel(BaseChannel):
    """Telegram 渠道实现

    使用 python-telegram-bot 库实现。
    """

    name = "telegram"

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._application = None
        self._message_handler: Callable[[str, str], Awaitable[None]] | None = None
        self._command_registry: dict[str, str] = {}

    def set_command_registry(self, commands: dict[str, str]) -> None:
        """设置命令注册表"""
        self._command_registry = commands

    async def start(self) -> None:
        """启动 Telegram Bot"""
        try:
            from telegram.ext import ApplicationBuilder
            from telegram.ext import MessageHandler, filters
            from telegram import Update, BotCommand
        except ImportError:
            raise ImportError(
                "python-telegram-bot is required for Telegram support. "
                "Install it with: uv pip install python-telegram-bot"
            )

        if not self.config.token:
            raise ValueError("Telegram bot token is required")

        # 创建 Application
        self._application = (
            ApplicationBuilder()
            .token(self.config.token)
            .build()
        )

        # 注册统一消息处理器（处理所有消息，包括命令）
        async def handle_all(update: Update, context):
            if not update.message or not update.message.text:
                return

            user_id = str(update.effective_user.id)

            # 检查用户权限
            if not self.is_user_allowed(user_id):
                await update.message.reply_text("Permission denied.")
                return

            # 所有消息（包括命令）统一转发给消息处理器
            if self._message_handler:
                await self._message_handler(user_id, update.message.text)

        # 添加消息处理器（不排除命令）
        self._application.add_handler(
            MessageHandler(filters.TEXT, handle_all)
        )

        # 注册命令菜单
        if self._command_registry:
            commands = [
                BotCommand(cmd, desc) for cmd, desc in self._command_registry.items()
            ]
            await self._application.bot.set_my_commands(commands)

        # 启动
        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()

    async def stop(self) -> None:
        """停止 Telegram Bot"""
        if self._application:
            await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()

    async def send_message(self, user_id: str, text: str) -> None:
        """发送消息给用户"""
        if not self._application:
            return

        # 分段发送长消息（Telegram 限制 4096 字符）
        max_length = 4000
        chunks = []
        while len(text) > max_length:
            # 尝试在合适的位置分割
            split_pos = text[:max_length].rfind("\n")
            if split_pos == -1:
                split_pos = max_length
            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")
        chunks.append(text)

        for chunk in chunks:
            try:
                await self._application.bot.send_message(
                    chat_id=int(user_id),
                    text=chunk,
                    parse_mode="Markdown",
                )
            except Exception:
                # 如果 Markdown 解析失败，尝试纯文本
                try:
                    await self._application.bot.send_message(
                        chat_id=int(user_id),
                        text=chunk,
                    )
                except Exception:
                    pass

    def on_message(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        """注册消息处理器"""
        self._message_handler = handler

    def is_user_allowed(self, user_id: str) -> bool:
        """检查用户是否被允许"""
        if not self.config.allow_from:
            return True
        return user_id in self.config.allow_from

    # ========== BaseChannel 接口实现（暂不使用）==========

    async def send_permission_keyboard(
        self, user_id: str, tool_name: str, tool_args: dict, callback_id: str
    ) -> None:
        """暂不实现权限键盘"""
        pass

    def on_permission_response(
        self, handler: Callable[[str, str, str], Awaitable[None]]
    ) -> None:
        """暂不实现权限响应"""
        pass
