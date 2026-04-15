"""Chat, interrupt, and status endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..deps import get_core, get_permission_handler
from ..events import SSEEventBridge
from ..schemas import ChatRequest, StatusResponse, MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(
    req: ChatRequest,
    core=Depends(get_core),
    perm_handler=Depends(get_permission_handler),
):
    """Send a message and stream agent events via SSE."""
    if core.is_agent_busy:
        core.queue_message(req.message)
        return {"queued": True}

    bridge = SSEEventBridge(core.event_bus)
    bridge.attach()
    perm_handler.set_bridge(bridge)

    async def run_chat():
        try:
            response = await core.chat(req.message, req.media)
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
            perm_handler.set_bridge(None)
            perm_handler.cancel_all()
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/interrupt", response_model=MessageResponse)
async def interrupt(core=Depends(get_core)):
    """Cancel current operation."""
    core.interrupt()
    return MessageResponse(ok=True)


@router.get("/status", response_model=StatusResponse)
async def status(core=Depends(get_core)):
    """Get agent busy state and current model/provider."""
    return StatusResponse(
        busy=core.is_agent_busy,
        model=core.current_model,
        provider=core.current_provider,
    )
