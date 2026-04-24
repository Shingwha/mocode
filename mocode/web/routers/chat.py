"""Chat, interrupt, and status endpoints (v0.2)."""

import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..deps import get_app
from ..events import SSEEventBridge
from ..schemas import ChatRequest, StatusResponse, MessageResponse
from ...app import App

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(
    req: ChatRequest,
    app: App = Depends(get_app),
):
    """Send a message and stream agent events via SSE."""
    if app.is_agent_busy:
        app.queue_message(req.message)
        return {"queued": True}

    bridge = SSEEventBridge(app.event_bus)
    bridge.attach()

    async def run_chat():
        try:
            response = await app.chat(req.message, req.media)
            bridge.send_done(response)
        except asyncio.CancelledError:
            bridge.stop()
        except Exception as e:
            logger.exception("Chat error")
            bridge.send_error(str(e))

    async def stream():
        task = asyncio.create_task(run_chat())
        try:
            async for event in bridge.events():
                yield event
        finally:
            bridge.detach()
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/interrupt", response_model=MessageResponse)
async def interrupt(app: App = Depends(get_app)):
    """Cancel current operation."""
    app.interrupt()
    return MessageResponse(ok=True)


@router.get("/status", response_model=StatusResponse)
async def status(app: App = Depends(get_app)):
    """Get agent busy state and current model/provider."""
    return StatusResponse(
        busy=app.is_agent_busy,
        model=app.current_model,
        provider=app.current_provider,
    )
