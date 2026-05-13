"""Channel Protocol — unified communication interface."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Protocol

from .types import InboundMessage, OutboundMessage


class Channel(Protocol):
    """Interface that platform channels must implement."""

    name: str

    async def start(
        self, on_message: Callable[[InboundMessage], Awaitable[None]]
    ) -> None: ...

    async def send(self, msg: OutboundMessage) -> None: ...

    async def stop(self) -> None: ...
