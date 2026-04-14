"""Session CRUD and history endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_core
from ..schemas import (
    SessionListResponse,
    SessionSaveResponse,
    SessionSummary,
    SessionDetail,
    HistoryResponse,
    MessageResponse,
)

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(core=Depends(get_core)):
    """List sessions (summaries, no messages)."""
    sessions = core.list_sessions()
    summaries = [
        SessionSummary(
            id=s.id,
            created_at=s.created_at,
            updated_at=s.updated_at,
            workdir=s.workdir,
            model=s.model,
            provider=s.provider,
            message_count=s.message_count,
        )
        for s in sessions
    ]
    return SessionListResponse(sessions=summaries)


@router.post("/sessions", response_model=SessionSaveResponse)
async def save_session(core=Depends(get_core)):
    """Save current session."""
    session = core.save_session()
    return SessionSaveResponse(
        session=SessionSummary(
            id=session.id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            workdir=session.workdir,
            model=session.model,
            provider=session.provider,
            message_count=session.message_count,
        )
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def load_session(session_id: str, core=Depends(get_core)):
    """Load a session by ID (sets as current)."""
    session = core.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(
        id=session.id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        workdir=session.workdir,
        model=session.model,
        provider=session.provider,
        message_count=session.message_count,
        messages=session.messages,
    )


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(session_id: str, core=Depends(get_core)):
    """Delete a session."""
    if not core.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return MessageResponse(ok=True)


@router.get("/history", response_model=HistoryResponse)
async def get_history(core=Depends(get_core)):
    """Get current message history."""
    return HistoryResponse(messages=core.messages)


@router.delete("/history", response_model=MessageResponse)
async def clear_history(core=Depends(get_core)):
    """Clear history (auto-saves first)."""
    core.clear_history_with_save()
    return MessageResponse(ok=True)
