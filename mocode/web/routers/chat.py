"""Chat, interrupt, and status endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_core
from ..schemas import ChatRequest, ChatResponse, StatusResponse, MessageResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, core=Depends(get_core)):
    """Send a message and block until response."""
    if core.is_agent_busy:
        raise HTTPException(status_code=409, detail="Agent is busy")
    response = await core.chat(req.message, req.media)
    return ChatResponse(response=response)


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
