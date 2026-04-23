"""Feishu/Lark channel using WebSocket long connection via lark-oapi SDK."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from ..base import BaseChannel
from ..bus import OutboundMessage
from ...config import Config
from .api import fetch_bot_open_id, get_message_content, reply_message, send_message
from .card import (
    build_card_elements,
    detect_msg_format,
    extract_post_content,
    extract_share_card_content,
    markdown_to_post,
    resolve_mentions,
    split_elements_by_table_limit,
)
from .config import get_feishu_config
from .media import (
    _AUDIO_EXTS,
    _IMAGE_EXTS,
    _VIDEO_EXTS,
    download_and_save,
    upload_file,
    upload_image,
)

if TYPE_CHECKING:
    import lark_oapi as lark

logger = logging.getLogger(__name__)

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None

MSG_TYPE_MAP: dict[str, str] = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class FeishuChannel(BaseChannel):
    """Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events — no public IP or webhook required.

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    """

    def __init__(
        self, name: str, config: Config, gateway_config: dict, bus: Any,
    ) -> None:
        super().__init__(name, config, gateway_config, bus)
        self._feishu_cfg = get_feishu_config(gateway_config)
        self._client: Any = None  # lark.Client
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: bool = False
        self._bot_open_id: str | None = None

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        app_id = self._feishu_cfg["app_id"]
        app_secret = self._feishu_cfg["app_secret"]
        if not app_id or not app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        import lark_oapi as lark
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        self._running = True
        self._loop = asyncio.get_running_loop()

        domain = LARK_DOMAIN if self._feishu_cfg["domain"] == "lark" else FEISHU_DOMAIN
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .domain(domain)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        encrypt_key = self._feishu_cfg["encrypt_key"]
        verification_token = self._feishu_cfg["verification_token"]

        builder = (
            lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
            .register_p2_im_message_receive_v1(self._on_message_sync)
        )
        # Register optional noise-suppression handlers
        for method_name, handler in (
            ("register_p2_im_message_reaction_created_v1", lambda d: None),
            ("register_p2_im_message_reaction_deleted_v1", lambda d: None),
            ("register_p2_im_message_message_read_v1", lambda d: None),
        ):
            method = getattr(builder, method_name, None)
            if callable(method):
                builder = method(handler)

        event_handler = builder.build()

        self._ws_client = lark.ws.Client(
            app_id,
            app_secret,
            domain=domain,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # Start WebSocket in a dedicated daemon thread with its own event loop.
        # lark_oapi's module-level `loop = asyncio.get_event_loop()` would
        # conflict with the main asyncio loop otherwise.
        def run_ws() -> None:
            import lark_oapi.ws.client as _lark_ws_client

            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws_client.loop = ws_loop
            try:
                while self._running:
                    try:
                        self._ws_client.start()
                    except Exception as e:
                        logger.warning("Feishu WebSocket error: %s", e)
                    if self._running:
                        import time
                        time.sleep(5)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        # Fetch bot's own open_id for @mention matching
        self._bot_open_id = await self._loop.run_in_executor(
            None, fetch_bot_open_id, self._client,
        )
        if self._bot_open_id:
            logger.info("Feishu bot open_id: %s", self._bot_open_id)
        else:
            logger.warning("Could not fetch bot open_id; @mention matching may be inaccurate")

        logger.info("Feishu bot started with WebSocket long connection")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Feishu bot."""
        self._running = False
        logger.info("Feishu bot stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu, including media if present."""
        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        try:
            receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"
            loop = asyncio.get_running_loop()

            # Determine reply routing
            reply_message_id: str | None = None
            if self._feishu_cfg["reply_to_message"] and not msg.metadata.get("_progress"):
                reply_message_id = msg.metadata.get("message_id")
            elif msg.metadata.get("thread_id"):
                reply_message_id = msg.metadata.get("root_id") or msg.metadata.get("message_id")

            first_send = True

            def _do_send(m_type: str, content: str) -> None:
                nonlocal first_send
                if reply_message_id and first_send:
                    first_send = False
                    ok = reply_message(self._client, reply_message_id, m_type, content)
                    if ok:
                        return
                send_message(self._client, receive_id_type, msg.chat_id, m_type, content)

            # Send media first
            for file_path in msg.media:
                if not os.path.isfile(file_path):
                    logger.warning("Media file not found: %s", file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in _IMAGE_EXTS:
                    key = await loop.run_in_executor(None, upload_image, self._client, file_path)
                    if key:
                        _do_send("image", json.dumps({"image_key": key}))
                else:
                    key = await loop.run_in_executor(None, upload_file, self._client, file_path)
                    if key:
                        if ext in _AUDIO_EXTS:
                            media_type = "audio"
                        elif ext in _VIDEO_EXTS:
                            media_type = "video"
                        else:
                            media_type = "file"
                        _do_send(media_type, json.dumps({"file_key": key}))

            # Send text content
            if msg.content and msg.content.strip():
                fmt = detect_msg_format(msg.content)

                if fmt == "text":
                    text_body = json.dumps({"text": msg.content.strip()}, ensure_ascii=False)
                    await loop.run_in_executor(None, _do_send, "text", text_body)

                elif fmt == "post":
                    post_body = markdown_to_post(msg.content)
                    await loop.run_in_executor(None, _do_send, "post", post_body)

                else:
                    elements = build_card_elements(msg.content)
                    for chunk in split_elements_by_table_limit(elements):
                        card = {"config": {"wide_screen_mode": True}, "elements": chunk}
                        await loop.run_in_executor(
                            None, _do_send, "interactive",
                            json.dumps(card, ensure_ascii=False),
                        )

        except Exception as e:
            logger.error("Error sending Feishu message: %s", e)
            raise

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    def _on_message_sync(self, data: Any) -> None:
        """Sync handler called from WebSocket thread; schedules async on main loop."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: Any) -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # Deduplication
            message_id = message.message_id
            if message_id in self._seen:
                return
            self._seen[message_id] = None
            while len(self._seen) > 1000:
                self._seen.popitem(last=False)

            # Skip bot messages
            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            # Group message filtering
            if chat_type == "group" and not self._is_group_message_for_bot(message):
                return

            # Parse content
            content_parts: list[str] = []
            media_paths: list[str] = []

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            if msg_type == "text":
                text = content_json.get("text", "")
                if text:
                    mentions = getattr(message, "mentions", None)
                    text = resolve_mentions(text, mentions)
                    content_parts.append(text)

            elif msg_type == "post":
                text, image_keys = extract_post_content(content_json)
                if text:
                    content_parts.append(text)
                for img_key in image_keys:
                    file_path, content_text = await download_and_save(
                        self._client, "image", {"image_key": img_key},
                        message_id, sender_id,
                    )
                    if file_path:
                        media_paths.append(file_path)
                    content_parts.append(content_text)

            elif msg_type in ("image", "audio", "file", "media"):
                file_path, content_text = await download_and_save(
                    self._client, msg_type, content_json, message_id, sender_id,
                )
                if file_path:
                    media_paths.append(file_path)

                if msg_type == "audio" and file_path:
                    transcription = await self._transcribe(file_path)
                    if transcription:
                        content_text = f"[transcription: {transcription}]"

                content_parts.append(content_text)

            elif msg_type in (
                "share_chat", "share_user", "interactive",
                "share_calendar_event", "system", "merge_forward",
            ):
                text = extract_share_card_content(content_json, msg_type)
                if text:
                    content_parts.append(text)

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            # Extract reply context
            parent_id = getattr(message, "parent_id", None) or None
            root_id = getattr(message, "root_id", None) or None
            thread_id = getattr(message, "thread_id", None) or None

            if parent_id and self._client:
                loop = asyncio.get_running_loop()
                reply_ctx = await loop.run_in_executor(
                    None, get_message_content, self._client, parent_id,
                )
                if reply_ctx:
                    content_parts.insert(0, reply_ctx)

            content = "\n".join(content_parts) if content_parts else ""
            if not content and not media_paths:
                return

            # Route: group → chat_id, p2p → sender_id
            reply_to = chat_id if chat_type == "group" else sender_id

            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                    "parent_id": parent_id,
                    "root_id": root_id,
                    "thread_id": thread_id,
                },
            )

        except Exception as e:
            logger.error("Error processing Feishu message: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_bot_mentioned(self, message: Any) -> bool:
        """Check if the bot is @mentioned in the message."""
        raw_content = message.content or ""
        if "@_all" in raw_content:
            return True

        for mention in getattr(message, "mentions", None) or []:
            mid = getattr(mention, "id", None)
            if not mid:
                continue
            mention_open_id = getattr(mid, "open_id", None) or ""
            if self._bot_open_id:
                if mention_open_id == self._bot_open_id:
                    return True
            else:
                if not getattr(mid, "user_id", None) and mention_open_id.startswith("ou_"):
                    return True
        return False

    def _is_group_message_for_bot(self, message: Any) -> bool:
        """Allow group messages when policy is open or bot is @mentioned."""
        if self._feishu_cfg["group_policy"] == "open":
            return True
        return self._is_bot_mentioned(message)

    async def _transcribe(self, audio_path: str) -> str:
        """Transcribe audio file using Whisper API."""
        try:
            from ..transcription import transcribe_audio
            api_key = self._config.api_key
            base_url = self._config.base_url or ""
            return await transcribe_audio(audio_path, api_key, base_url)
        except Exception as e:
            logger.warning("Transcription failed: %s", e)
            return ""
