"""WeChat (Weixin) channel — standalone protocol layer with persistent state"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

import httpx

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

if TYPE_CHECKING:
    from ...app.config import ProviderConfig
    from ..types import OutboundMessage

logger = logging.getLogger(__name__)


@dataclass
class WeixinMessage:
    """A message received from WeChat."""

    channel: str  # always "weixin"
    sender_id: str
    chat_id: str
    content: str
    media: list[str] = field(default_factory=list)  # local file paths
    metadata: dict = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"


class WeixinChannel:
    """WeChat channel using direct HTTP long-poll API (ilinkai).

    Usage::

        # First time: QR login
        channel = WeixinChannel(
            state_dir=Path.home() / ".mocode" / "weixin",
            media_dir=Path.home() / ".mocode" / "media" / "weixin",
        )
        if await channel.login():
            ...  # token auto-saved to state_dir/state.json

        # Restore session (token loaded from state_dir)
        channel = WeixinChannel(state_dir=..., media_dir=...)
        await channel.start(on_message)
    """

    @property
    def name(self) -> str:
        return "weixin"

    def __init__(
        self,
        token: str = "",
        base_url: str = "https://ilinkai.weixin.qq.com",
        state_dir: Path | None = None,
        media_dir: Path | None = None,
        provider_config: ProviderConfig | None = None,
    ) -> None:
        self._state = WeixinState(state_dir)
        self._state.token = token
        self._state.base_url = base_url
        self._media_dir = media_dir or Path.home() / ".mocode" / "media" / "weixin"
        self._provider_config = provider_config
        self._client: httpx.AsyncClient | None = None
        self._api: WeixinApi | None = None
        self._login: LoginHandler | None = None
        self._typing: TypingHandler | None = None
        self._media: MediaHandler | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._running: bool = False
        self._consecutive_failures: int = 0
        self._session_pause_until: float = 0.0
        self._poll_timeout_s: int = POLL_TIMEOUT_S

    @property
    def token(self) -> str:
        return self._state.token

    @property
    def base_url(self) -> str:
        return self._state.base_url

    async def login(self) -> bool:
        """QR code login or verify existing token. Returns True on success."""
        # Try loading saved state first
        if not self._state.token:
            self._state.load()

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._poll_timeout_s + 10, connect=30),
            follow_redirects=True,
        )
        self._api = WeixinApi(self._client, self._state.base_url)
        self._login = LoginHandler(self._api, self._state)
        self._typing = TypingHandler(self._api, self._state)
        self._media = MediaHandler(self._api, self._client, self._state, self._media_dir)

        if await self._login.run():
            self._state.save()
            logger.info("WeChat login successful")
            return True
        return False

    async def start(
        self, callback: Callable[[WeixinMessage], Awaitable[None]]
    ) -> None:
        """Start polling for messages. Each message triggers the callback."""
        if not self._api or not self._state.token:
            raise RuntimeError("Not logged in. Call login() first.")
        self._running = True
        self._callback = callback
        logger.info("WeChat channel starting poll loop")
        await self._poll_loop()

    async def send(self, msg: OutboundMessage) -> None:
        """Send outbound message — media files first, then text."""
        if not self._api or not self._state.token:
            logger.warning("WeChat not initialized, cannot send")
            return
        if self._session_pause_remaining() > 0:
            return

        chat_id = msg.chat_id
        ctx_token = self._state.context_tokens.get(chat_id, "")
        if not ctx_token:
            logger.warning("No context_token for chat_id=%s, cannot send", chat_id)
            return

        assert self._typing is not None
        await self._typing.stop(chat_id, clear_remote=True)

        typing_ticket = await self._typing.get_ticket(chat_id, ctx_token)
        keepalive_stop = asyncio.Event()
        keepalive_task: asyncio.Task | None = None
        if typing_ticket:
            try:
                await self._api.send_typing(
                    self._state.token, chat_id, typing_ticket,
                    TYPING_STATUS_TYPING,
                )
            except Exception:
                pass
            keepalive_task = asyncio.create_task(
                self._typing._keepalive(
                    chat_id, typing_ticket, keepalive_stop
                )
            )

        try:
            # Send media files first
            assert self._media is not None
            for media_path in msg.media:
                try:
                    await self._media.upload(media_path, chat_id, ctx_token)
                except Exception as e:
                    logger.error("WeChat send media error for %s: %s", media_path, e)

            # Send text content
            content = (msg.content or "").strip()
            if content:
                for chunk in self._split_message(content, 3500):
                    await self._api.send_message(
                        self._state.token, chat_id, ctx_token, chunk
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
                        self._state.token, chat_id, typing_ticket,
                        TYPING_STATUS_CANCEL,
                    )
                except Exception:
                    pass

    async def stop(self) -> None:
        """Stop polling, close connections, save state."""
        self._running = False
        if self._typing:
            self._typing.cancel_all()
        if self._client:
            await self._client.aclose()
            self._client = None
        self._state.save()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        assert self._api is not None

        while self._running:
            try:
                remaining = self._session_pause_remaining()
                if remaining > 0:
                    await asyncio.sleep(remaining)

                assert self._client is not None
                self._client.timeout = httpx.Timeout(
                    self._poll_timeout_s + 10, connect=30
                )

                msgs, new_cursor, timeout_ms = await self._api.get_updates(
                    self._state.token,
                    cursor=self._state.poll_cursor,
                    timeout=self._poll_timeout_s,
                )

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
        if msg.get("message_type") == MESSAGE_TYPE_BOT:
            return

        msg_id = self._dedup_id(msg)
        if msg_id in self._seen:
            return
        self._seen[msg_id] = None
        while len(self._seen) > 1000:
            self._seen.popitem(last=False)

        from_user_id = str(msg.get("from_user_id", "") or "")
        if not from_user_id:
            return

        self._cache_context_token(from_user_id, msg)

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

        assert self._typing is not None
        ctx_token = self._state.context_tokens.get(from_user_id, "")
        await self._typing.start(from_user_id, ctx_token)

        wx_msg = WeixinMessage(
            channel="weixin",
            sender_id=from_user_id,
            chat_id=from_user_id,
            content=content,
            media=media_paths,
            metadata={"message_id": msg_id},
        )
        await self._callback(wx_msg)

    @staticmethod
    def _dedup_id(msg: dict) -> str:
        msg_id = str(msg.get("message_id", "") or msg.get("seq", ""))
        if not msg_id:
            msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
        return msg_id

    def _cache_context_token(self, user_id: str, msg: dict) -> None:
        ctx_token = msg.get("context_token", "")
        if ctx_token:
            self._state.context_tokens[user_id] = ctx_token
            self._state.save()

    async def _handle_text_item(
        self, item: dict, uid: str, parts: list[str], media: list[str]
    ) -> None:
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

    async def _handle_media_item(
        self, item: dict, uid: str, parts: list[str], media: list[str]
    ) -> None:
        """Handle image, file, and video items — download and save."""
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
        if not self._provider_config:
            logger.warning("No provider_config set, cannot transcribe")
            return ""
        try:
            from .transcription import transcribe_audio
            return await transcribe_audio(
                audio_path,
                self._provider_config.api_key,
                self._provider_config.base_url or "",
            )
        except Exception as e:
            logger.warning("Transcription failed: %s", e)
            return ""

    @staticmethod
    def _format_quote(ref: dict) -> str | None:
        ref_item = ref.get("message_item")
        segments: list[str] = []
        if ref.get("title"):
            segments.append(ref["title"])
        if ref_item:
            ref_text = (ref_item.get("text_item") or {}).get("text", "")
            if ref_text:
                segments.append(ref_text)
        return " | ".join(segments) if segments else None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _split_message(text: str, max_length: int = 3500) -> list[str]:
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
