"""WeChat typing indicator handler"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from .api import (
    CONFIG_CACHE_INITIAL_RETRY_S,
    CONFIG_CACHE_MAX_RETRY_S,
    TYPING_KEEPALIVE_INTERVAL_S,
    TYPING_STATUS_CANCEL,
    TYPING_STATUS_TYPING,
    TYPING_TICKET_TTL_S,
    WeixinApi,
)
from .state import WeixinState

logger = logging.getLogger(__name__)


class TypingHandler:
    """Manages WeChat typing indicators with keepalive and ticket caching."""

    def __init__(self, api: WeixinApi, state: WeixinState) -> None:
        self._api = api
        self._state = state
        self._typing_tasks: dict[str, asyncio.Task] = {}

    def cancel_all(self) -> None:
        """Cancel all active typing tasks."""
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

    async def get_ticket(
        self, user_id: str, context_token: str = ""
    ) -> str:
        """Get typing ticket with per-user refresh + failure backoff."""
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

    async def start(self, chat_id: str, context_token: str = "") -> None:
        """Start typing indicator when a message is received."""
        if not self._api or not self._state.token or not chat_id:
            return
        await self.stop(chat_id, clear_remote=False)
        ticket = await self.get_ticket(chat_id, context_token)
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
            self._keepalive(chat_id, ticket, stop_event)
        )
        task._typing_stop_event = stop_event  # type: ignore[attr-defined]
        self._typing_tasks[chat_id] = task

    async def stop(self, chat_id: str, *, clear_remote: bool) -> None:
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

    async def _keepalive(
        self, chat_id: str, ticket: str, stop_event: asyncio.Event
    ) -> None:
        """Periodically refresh typing indicator."""
        try:
            while not stop_event.is_set():
                await asyncio.sleep(TYPING_KEEPALIVE_INTERVAL_S)
                if stop_event.is_set():
                    break
                try:
                    await self._api.send_typing(
                        self._state.token, chat_id, ticket,
                        TYPING_STATUS_TYPING,
                    )
                except Exception:
                    pass
        finally:
            pass
