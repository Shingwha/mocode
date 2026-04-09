"""WeChat (Weixin) channel using direct HTTP long-poll API"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections import OrderedDict

import httpx

from ..base import BaseChannel
from ..bus import OutboundMessage
from ...core.config import Config
from .api import (
    BACKOFF_DELAY_S,
    BASE_INFO,
    CONFIG_CACHE_INITIAL_RETRY_S,
    CONFIG_CACHE_MAX_RETRY_S,
    ERRCODE_SESSION_EXPIRED,
    ITEM_TEXT,
    MAX_CONSECUTIVE_FAILURES,
    MAX_QR_REFRESH_COUNT,
    MESSAGE_TYPE_BOT,
    POLL_TIMEOUT_S,
    RETRY_DELAY_S,
    SESSION_PAUSE_DURATION_S,
    TYPING_KEEPALIVE_INTERVAL_S,
    TYPING_STATUS_CANCEL,
    TYPING_STATUS_TYPING,
    TYPING_TICKET_TTL_S,
    WeixinApi,
    WeixinApiError,
)
from .state import WeixinState

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
        self._typing_tasks: dict[str, asyncio.Task] = {}
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

        await self._login_flow()
        if not self._running:
            return

        logger.info("WeChat channel starting poll loop")
        await self._poll_loop()

    async def stop(self) -> None:
        """Cancel tasks, close client, save state."""
        self._running = False
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
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
        await self._stop_typing(msg.chat_id, clear_remote=True)

        # Get typing ticket and start keepalive for long sends
        typing_ticket = await self._get_typing_ticket(msg.chat_id, ctx_token)
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
                self._typing_keepalive(
                    msg.chat_id, typing_ticket, keepalive_stop
                )
            )

        try:
            for chunk in self._split_message(msg.content, 3500):
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
    # Login
    # ------------------------------------------------------------------

    async def _login_flow(self) -> None:
        """Try restore token, verify, QR login if needed."""
        if self._state.load():
            # Verify token with instant poll
            try:
                assert self._api is not None
                _, _, _ = await self._api.get_updates(
                    self._state.token, cursor=self._state.poll_cursor,
                    timeout=1,
                )
                logger.info("WeChat token restored from state")
                return
            except Exception:
                logger.info("Saved token expired, re-login required")

        if not await self._qr_login():
            logger.error("WeChat login failed")
            self._running = False

    async def _qr_login(self) -> bool:
        """Perform QR code login flow. Returns True on success."""
        assert self._api is not None
        refresh_count = 0
        try:
            qrcode_id, scan_url = await self._api.get_qrcode()
            self._print_qr_code(scan_url)
            current_poll_base: str | None = None

            while self._running:
                try:
                    status_data = await self._api.check_qr_status(
                        qrcode_id, base_url=current_poll_base
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    await asyncio.sleep(1)
                    continue
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500:
                        await asyncio.sleep(1)
                        continue
                    raise

                if not isinstance(status_data, dict):
                    await asyncio.sleep(1)
                    continue

                status = status_data.get("status", "")
                if status == "confirmed":
                    token = status_data.get("bot_token", "")
                    base_url = status_data.get("baseurl", "")
                    if not token:
                        logger.error("Login confirmed but no token in response")
                        return False
                    self._state.token = token
                    if base_url:
                        self._state.base_url = base_url
                        self._api.base_url = base_url
                    self._state.save()
                    logger.info("WeChat login successful")
                    return True

                elif status == "scaned_but_redirect":
                    redirect_host = str(
                        status_data.get("redirect_host", "") or ""
                    ).strip()
                    if redirect_host:
                        if not redirect_host.startswith(("http://", "https://")):
                            redirect_host = f"https://{redirect_host}"
                        current_poll_base = redirect_host

                elif status == "expired":
                    refresh_count += 1
                    if refresh_count > MAX_QR_REFRESH_COUNT:
                        logger.warning("QR expired too many times, giving up")
                        return False
                    qrcode_id, scan_url = await self._api.get_qrcode()
                    current_poll_base = None
                    self._print_qr_code(scan_url)

                # "wait" status: keep polling
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("WeChat QR login failed: %s", e)
        return False

    @staticmethod
    def _print_qr_code(url: str) -> None:
        """Print QR code or URL for scanning."""
        try:
            import qrcode as qr_lib

            qr = qr_lib.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            print(f"\nWeChat Login URL: {url}\n")

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
                    except Exception:
                        pass

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
        msg_id = str(msg.get("message_id", "") or msg.get("seq", ""))
        if not msg_id:
            msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
        if msg_id in self._seen:
            return
        self._seen[msg_id] = None
        while len(self._seen) > 1000:
            self._seen.popitem(last=False)

        from_user_id = str(msg.get("from_user_id", "") or "")
        if not from_user_id:
            return

        # Cache context_token
        ctx_token = msg.get("context_token", "")
        if ctx_token:
            self._state.context_tokens[from_user_id] = ctx_token
            self._state.save()

        # Extract text content (Phase 1: text only)
        item_list: list[dict] = msg.get("item_list") or []
        content_parts: list[str] = []

        for item in item_list:
            if item.get("type", 0) == ITEM_TEXT:
                text = (item.get("text_item") or {}).get("text", "")
                if text:
                    # Handle quoted messages
                    ref = item.get("ref_msg")
                    if ref:
                        ref_item = ref.get("message_item")
                        if ref_item and ref_item.get("type", 0) in (
                            2, 3, 4, 5  # non-text types
                        ):
                            content_parts.append(text)
                        else:
                            parts: list[str] = []
                            if ref.get("title"):
                                parts.append(ref["title"])
                            if ref_item:
                                ref_text = (
                                    ref_item.get("text_item") or {}
                                ).get("text", "")
                                if ref_text:
                                    parts.append(ref_text)
                            if parts:
                                content_parts.append(
                                    f"[Quote: {' | '.join(parts)}]\n{text}"
                                )
                            else:
                                content_parts.append(text)
                    else:
                        content_parts.append(text)

        content = "\n".join(content_parts)
        if not content:
            return

        logger.info(
            "WeChat inbound: from=%s items=%d len=%d",
            from_user_id,
            len(item_list),
            len(content),
        )

        # Start typing indicator
        await self._start_typing(from_user_id, ctx_token)

        await self._handle_message(
            sender_id=from_user_id,
            chat_id=from_user_id,
            content=content,
            metadata={"message_id": msg_id},
        )

    # ------------------------------------------------------------------
    # Typing
    # ------------------------------------------------------------------

    async def _get_typing_ticket(
        self, user_id: str, context_token: str = ""
    ) -> str:
        """Get typing ticket with per-user refresh + failure backoff."""
        assert self._api is not None
        now = time.time()
        entry = self._state.typing_tickets.get(user_id)
        if entry and now < float(entry.get("next_fetch_at", 0)):
            return str(entry.get("ticket", "") or "")

        try:
            data = await self._api.get_config(
                self._state.token, user_id, context_token
            )
            if data.get("ret", 0) == 0:
                ticket = str(data.get("typing_ticket", "") or "")
                self._state.typing_tickets[user_id] = {
                    "ticket": ticket,
                    "ever_succeeded": True,
                    "next_fetch_at": now + random.random() * TYPING_TICKET_TTL_S,
                    "retry_delay_s": CONFIG_CACHE_INITIAL_RETRY_S,
                }
                return ticket
        except Exception:
            pass

        # Backoff on failure
        prev_delay = (
            float(entry.get("retry_delay_s", CONFIG_CACHE_INITIAL_RETRY_S))
            if entry
            else CONFIG_CACHE_INITIAL_RETRY_S
        )
        next_delay = min(prev_delay * 2, CONFIG_CACHE_MAX_RETRY_S)
        if entry:
            entry["next_fetch_at"] = now + next_delay
            entry["retry_delay_s"] = next_delay
            return str(entry.get("ticket", "") or "")

        self._state.typing_tickets[user_id] = {
            "ticket": "",
            "ever_succeeded": False,
            "next_fetch_at": now + CONFIG_CACHE_INITIAL_RETRY_S,
            "retry_delay_s": CONFIG_CACHE_INITIAL_RETRY_S,
        }
        return ""

    async def _start_typing(
        self, chat_id: str, context_token: str = ""
    ) -> None:
        """Start typing indicator when a message is received."""
        if not self._api or not self._state.token or not chat_id:
            return
        await self._stop_typing(chat_id, clear_remote=False)
        ticket = await self._get_typing_ticket(chat_id, context_token)
        if not ticket:
            return
        try:
            await self._api.send_typing(
                self._state.token, chat_id, ticket, TYPING_STATUS_TYPING
            )
        except Exception:
            return

        stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._typing_keepalive(chat_id, ticket, stop_event)
        )
        task._typing_stop_event = stop_event  # type: ignore[attr-defined]
        self._typing_tasks[chat_id] = task

    async def _stop_typing(
        self, chat_id: str, *, clear_remote: bool
    ) -> None:
        """Stop typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            stop_event = getattr(task, "_typing_stop_event", None)
            if stop_event:
                stop_event.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if not clear_remote:
            return
        entry = self._state.typing_tickets.get(chat_id)
        ticket = str(entry.get("ticket", "") or "") if isinstance(entry, dict) else ""
        if not ticket or not self._api:
            return
        try:
            await self._api.send_typing(
                self._state.token, chat_id, ticket, TYPING_STATUS_CANCEL
            )
        except Exception:
            pass

    async def _typing_keepalive(
        self, chat_id: str, ticket: str, stop_event: asyncio.Event
    ) -> None:
        """Periodically refresh typing indicator."""
        try:
            while not stop_event.is_set():
                await asyncio.sleep(TYPING_KEEPALIVE_INTERVAL_S)
                if stop_event.is_set():
                    break
                try:
                    assert self._api is not None
                    await self._api.send_typing(
                        self._state.token, chat_id, ticket,
                        TYPING_STATUS_TYPING,
                    )
                except Exception:
                    pass
        finally:
            pass

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
