"""WeChat (Weixin) gateway implementation"""

import asyncio
import logging

from .base import BaseGateway
from ..core.config import Config

logger = logging.getLogger(__name__)


class WeixinGateway(BaseGateway):
    """Gateway for WeChat via weixin-bot-sdk.

    Requires: pip install weixin-bot-sdk>=0.2.0
    Launch: mocode gateway --type weixin
    """

    def __init__(self, config: Config, gateway_config: dict):
        super().__init__(config, gateway_config)
        self._bot = None
        self._typing_tasks: dict[str, asyncio.Task] = {}

    async def _setup(self) -> None:
        """Initialize WeChat bot with QR login."""
        try:
            from weixin_bot import WeixinBot
        except ImportError:
            raise ImportError(
                "weixin-bot-sdk is required for WeChat gateway. "
                "Install with: pip install weixin-bot-sdk>=0.2.0"
            )

        self._bot = WeixinBot()

        @self._bot.on_message
        async def on_msg(msg):
            await self._on_message(msg)

        logger.info("Scanning QR code to login...")
        await self._bot._login()
        logger.info("WeChat login successful")

    async def _run(self) -> None:
        """Long-polling loop with auto-reconnect."""
        while self._running:
            try:
                await self._bot._run_loop()
            except Exception as e:
                if not self._running:
                    break
                logger.error("WeChat bot error: %s, reconnecting in 30s...", e)
                await asyncio.sleep(30)
                try:
                    await self._bot._login()
                except Exception as login_err:
                    logger.error("Re-login failed: %s", login_err)

    async def _teardown(self) -> None:
        """Cancel all typing tasks."""
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

    async def _on_message(self, msg) -> None:
        """Handle incoming WeChat message."""
        if msg.type != "text":
            return

        user_id = str(msg.user_id)
        session = self._router.get_or_create(user_id)

        async def reply_fn(text: str):
            for chunk in self._split_message(text, 3500):
                await self._bot.reply(msg, chunk)

        async with session.lock:
            # Start typing refresher for long operations
            typing_task = asyncio.create_task(
                self._typing_refresher(user_id, interval=10.0)
            )
            self._typing_tasks[user_id] = typing_task
            try:
                await self.handle_message(user_id, msg.text, reply_fn)
            finally:
                typing_task.cancel()
                self._typing_tasks.pop(user_id, None)

    async def _send_typing(self, user_id: str, is_typing: bool) -> None:
        """Send or stop typing indicator."""
        if not self._bot:
            return
        try:
            if is_typing:
                await self._bot.send_typing(user_id)
            else:
                await self._bot.stop_typing(user_id)
        except Exception as e:
            logger.debug("Typing indicator failed for %s: %s", user_id, e)

    async def _typing_refresher(self, user_id: str, interval: float = 10.0) -> None:
        """Periodically refresh typing indicator during long operations."""
        while True:
            await asyncio.sleep(interval)
            await self._send_typing(user_id, True)

    @staticmethod
    def _split_message(text: str, max_length: int = 3500) -> list[str]:
        """Split long messages by newlines, respecting max length."""
        if len(text) <= max_length:
            return [text]

        chunks = []
        lines = text.split("\n")
        current = ""

        for line in lines:
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
                # Handle single line exceeding max_length
                if len(line) > max_length:
                    for i in range(0, len(line), max_length):
                        chunks.append(line[i : i + max_length])
                    current = ""
                else:
                    current = line
            else:
                current = current + "\n" + line if current else line

        if current:
            chunks.append(current)

        return chunks if chunks else [text]
