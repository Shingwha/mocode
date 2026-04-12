"""WeChat (Weixin) channel using direct HTTP long-poll API"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

import httpx

from ..base import BaseChannel
from ..bus import OutboundMessage
from ...core.config import Config
from .api import (
    BACKOFF_DELAY_S,
    ERRCODE_SESSION_EXPIRED,
    ITEM_FILE,
    ITEM_IMAGE,
    ITEM_TEXT,
    ITEM_VIDEO,
    ITEM_VOICE,
    MAX_CONSECUTIVE_FAILURES,
    MESSAGE_TYPE_BOT,
    POLL_TIMEOUT_S,
    RETRY_DELAY_S,
    SESSION_PAUSE_DURATION_S,
    TYPING_STATUS_CANCEL,
    TYPING_STATUS_TYPING,
    WeixinApi,
    WeixinApiError,
)
from .login import LoginHandler
from .media_handler import MediaHandler
from .state import WeixinState
from .typing import TypingHandler

logger = logging.getLogger(__name__)


class WeixinChannel(BaseChannel):
    """Channel for WeChat via direct HTTP API (ilinkai.weixin.qq.com).

    Uses QR code login to obtain a bot token, then long-polls for messages.
    No weixin-bot-sdk dependency required.

    Launch: mocode gateway --type weixin
    """

    def __init__(
        self, name: str, config: Config, gateway_config: dict, bus
    ) -> None:
        super().__init__(name, config, gateway_config, bus)
        self._client: httpx.AsyncClient | None = None
        self._api: WeixinApi | None = None
        self._state = WeixinState()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._login: LoginHandler | None = None
        self._typing: TypingHandler | None = None
        self._media: MediaHandler | None = None
        self._running: bool = False
        self._consecutive_failures: int = 0
        self._session_pause_until: float = 0.0
        self._poll_timeout_s: int = POLL_TIMEOUT_S

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Init client, load state, login if needed, start poll loop."""
        self._running = True
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._poll_timeout_s + 10, connect=30),
            follow_redirects=True,
        )
        self._api = WeixinApi(self._client, self._state.base_url)

        self._login = LoginHandler(self._api, self._state)
        self._typing = TypingHandler(self._api, self._state)
        self._media = MediaHandler(self._api, self._client, self._state)

        if not await self._login.run():
            return
        if not self._running:
            return

        logger.info("WeChat channel starting poll loop")
        await self._poll_loop()

    async def stop(self) -> None:
        """Cancel tasks, close client, save state."""
        self._running = False
        if self._typing:
            self._typing.cancel_all()
        if self._client:
            await self._client.aclose()
            self._client = None
        self._state.save()

    async def send(self, msg: OutboundMessage) -> None:
        """Send outbound message to WeChat user."""
        if not self._api or not self._state.token:
            logger.warning("WeChat not initialized, cannot send")
            return

        # Check session pause
        if self._session_pause_remaining() > 0:
            return

        ctx_token = self._state.context_tokens.get(msg.chat_id, "")
        if not ctx_token:
            logger.warning(
                "No context_token for chat_id=%s, cannot send", msg.chat_id
            )
            return

        # Stop typing before sending
        assert self._typing is not None
        await self._typing.stop(msg.chat_id, clear_remote=True)

        # Get typing ticket and start keepalive for long sends
        typing_ticket = await self._typing.get_ticket(msg.chat_id, ctx_token)
        keepalive_stop = asyncio.Event()
        keepalive_task: asyncio.Task | None = None
        if typing_ticket:
            try:
                await self._api.send_typing(
                    self._state.token, msg.chat_id, typing_ticket,
                    TYPING_STATUS_TYPING,
                )
            except Exception:
                pass
            keepalive_task = asyncio.create_task(
                self._typing._keepalive(
                    msg.chat_id, typing_ticket, keepalive_stop
                )
            )

        try:
            # Send media files first (matching nanobot pattern)
            assert self._media is not None
            for media_path in msg.media:
                try:
                    await self._media.upload(media_path, msg.chat_id, ctx_token)
                except Exception as e:
                    logger.error("WeChat send media error for %s: %s", media_path, e)

            # Send text content only if non-empty
            content = msg.content.strip()
            if content:
                for chunk in self._split_message(content, 3500):
                    await self._api.send_message(
                        self._state.token, msg.chat_id, ctx_token, chunk
                    )
        except Exception as e:
            logger.error("WeChat send error: %s", e)
        finally:
            keepalive_stop.set()
            if keepalive_task:
                keepalive_task.cancel()
                try:
                    await keepalive_task
                except asyncio.CancelledError:
                    pass
            if typing_ticket:
                try:
                    await self._api.send_typing(
                        self._state.token, msg.chat_id, typing_ticket,
                        TYPING_STATUS_CANCEL,
                    )
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Long-poll with error handling and backoff."""
        assert self._api is not None

        while self._running:
            try:
                # Handle session pause
                remaining = self._session_pause_remaining()
                if remaining > 0:
                    await asyncio.sleep(remaining)

                # Adjust client timeout
                assert self._client is not None
                self._client.timeout = httpx.Timeout(
                    self._poll_timeout_s + 10, connect=30
                )

                msgs, new_cursor, timeout_ms = await self._api.get_updates(
                    self._state.token,
                    cursor=self._state.poll_cursor,
                    timeout=self._poll_timeout_s,
                )

                # Update poll timeout from server suggestion
                if timeout_ms and timeout_ms > 0:
                    self._poll_timeout_s = max(timeout_ms // 1000, 5)

                if new_cursor:
                    self._state.poll_cursor = new_cursor
                    self._state.save()

                for msg in msgs:
                    try:
                        await self._process_message(msg)
                    except Exception as e:
                        logger.warning("Error processing message: %s", e)

                self._consecutive_failures = 0

            except asyncio.CancelledError:
                break
            except WeixinApiError as e:
                if not self._running:
                    break
                if e.code == ERRCODE_SESSION_EXPIRED:
                    self._pause_session()
                    logger.warning(
                        "WeChat session expired, pausing %d min",
                        SESSION_PAUSE_DURATION_S // 60,
                    )
                    continue
                self._consecutive_failures += 1
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._consecutive_failures = 0
                    await asyncio.sleep(BACKOFF_DELAY_S)
                else:
                    await asyncio.sleep(RETRY_DELAY_S)

            except httpx.TimeoutException:
                # Normal for long-poll
                continue
            except Exception:
                if not self._running:
                    break
                self._consecutive_failures += 1
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._consecutive_failures = 0
                    await asyncio.sleep(BACKOFF_DELAY_S)
                else:
                    await asyncio.sleep(RETRY_DELAY_S)

    def _pause_session(self, duration_s: int = SESSION_PAUSE_DURATION_S) -> None:
        self._session_pause_until = time.time() + duration_s

    def _session_pause_remaining(self) -> int:
        remaining = int(self._session_pause_until - time.time())
        if remaining <= 0:
            self._session_pause_until = 0.0
            return 0
        return remaining

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def _process_message(self, msg: dict) -> None:
        """Process a single message from getUpdates."""
        # Skip bot's own messages
        if msg.get("message_type") == MESSAGE_TYPE_BOT:
            return

        # Deduplication
        msg_id = self._dedup_id(msg)
        if msg_id in self._seen:
            return
        self._seen[msg_id] = None
        while len(self._seen) > 1000:
            self._seen.popitem(last=False)

        from_user_id = str(msg.get("from_user_id", "") or "")
        if not from_user_id:
            return

        # Cache context_token
        self._cache_context_token(from_user_id, msg)

        # Extract content from all item types
        item_list: list[dict] = msg.get("item_list") or []
        content_parts: list[str] = []
        media_paths: list[str] = []

        for item in item_list:
            handler = {
                ITEM_TEXT: self._handle_text_item,
                ITEM_IMAGE: self._handle_media_item,
                ITEM_VOICE: self._handle_voice_item,
                ITEM_FILE: self._handle_media_item,
                ITEM_VIDEO: self._handle_media_item,
            }.get(item.get("type", 0))
            if handler:
                await handler(item, from_user_id, content_parts, media_paths)

        content = "\n".join(content_parts)
        if not content and not media_paths:
            return

        logger.info(
            "WeChat inbound: from=%s items=%d len=%d media=%d",
            from_user_id,
            len(item_list),
            len(content),
            len(media_paths),
        )

        # Start typing indicator
        assert self._typing is not None
        ctx_token = self._state.context_tokens.get(from_user_id, "")
        await self._typing.start(from_user_id, ctx_token)

        await self._handle_message(
            sender_id=from_user_id,
            chat_id=from_user_id,
            content=content,
            media=media_paths or None,
            metadata={"message_id": msg_id},
        )

    @staticmethod
    def _dedup_id(msg: dict) -> str:
        """Generate a deduplication ID for a message."""
        msg_id = str(msg.get("message_id", "") or msg.get("seq", ""))
        if not msg_id:
            msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
        return msg_id

    def _cache_context_token(self, user_id: str, msg: dict) -> None:
        """Cache context_token from message if present."""
        ctx_token = msg.get("context_token", "")
        if ctx_token:
            self._state.context_tokens[user_id] = ctx_token
            self._state.save()

    async def _handle_text_item(
        self, item: dict, uid: str, parts: list[str], media: list[str]
    ) -> None:
        """Handle text items, including quoted messages."""
        text = (item.get("text_item") or {}).get("text", "")
        if not text:
            return
        ref = item.get("ref_msg")
        if not ref:
            parts.append(text)
            return
        ref_item = ref.get("message_item")
        if ref_item and ref_item.get("type", 0) in (2, 3, 4, 5):
            parts.append(text)
        else:
            quote = self._format_quote(ref)
            if quote:
                parts.append(f"[Quote: {quote}]\n{text}")
            else:
                parts.append(text)

    @staticmethod
    def _format_quote(ref: dict) -> str | None:
        """Format a quoted message reference."""
        ref_item = ref.get("message_item")
        segments: list[str] = []
        if ref.get("title"):
            segments.append(ref["title"])
        if ref_item:
            ref_text = (ref_item.get("text_item") or {}).get("text", "")
            if ref_text:
                segments.append(ref_text)
        return " | ".join(segments) if segments else None

    async def _handle_media_item(
        self, item: dict, uid: str, parts: list[str], media: list[str]
    ) -> None:
        """Handle image, file, and video items."""
        item_type = item.get("type", 0)
        labels = {
            ITEM_IMAGE: "image",
            ITEM_FILE: "file",
            ITEM_VIDEO: "video",
        }
        label = labels.get(item_type, "media")

        local = await self._download_media(item, uid)
        if local:
            media.append(local)
            if item_type == ITEM_FILE:
                fname = local.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                parts.append(f"[file: {fname}] (path: {local})")
            else:
                parts.append(f"[{label}]")
        else:
            parts.append(f"[{label}: download failed]")

    async def _handle_voice_item(
        self, item: dict, uid: str, parts: list[str], media: list[str]
    ) -> None:
        """Handle voice items with transcription."""
        local = await self._download_media(item, uid)
        if local:
            media.append(local)
            transcription = await self._transcribe(local)
            if transcription:
                parts.append(f"[voice] {transcription}")
            else:
                parts.append("[voice]")
        else:
            parts.append("[voice: download failed]")

    async def _download_media(self, item: dict, user_id: str) -> str | None:
        """Download media via MediaHandler."""
        assert self._media is not None
        return await self._media.download(item, user_id)

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

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _split_message(text: str, max_length: int = 3500) -> list[str]:
        """Split long messages by newlines, respecting max length."""
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        lines = text.split("\n")
        current = ""

        for line in lines:
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
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
