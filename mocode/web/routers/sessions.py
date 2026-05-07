"""Session CRUD and history endpoints (v0.2)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_app
from ..schemas import (
    SessionListResponse,
    SessionSaveResponse,
    SessionSummary,
    SessionDetail,
    HistoryResponse,
    MessageResponse,
)
from ...app import App

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    source: str | None = Query(None),
    channel: str | None = Query(None),
    app: App = Depends(get_app),
):
    """List sessions (summaries, no messages)."""
    sessions = app.list_sessions(source=source, channel=channel)
    summaries = [
        SessionSummary(
            id=s.id,
            created_at=s.created_at,
            updated_at=s.updated_at,
            workdir=s.workdir,
            model=s.model,
            provider=s.provider,
            message_count=s.message_count,
            title=s.title,
        )
        for s in sessions
    ]
    return SessionListResponse(sessions=summaries)


@router.post("/sessions", response_model=SessionSaveResponse)
async def save_session(app: App = Depends(get_app)):
    """Save current session."""
    session = app.save_session()
    return SessionSaveResponse(
        session=SessionSummary(
            id=session.id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            workdir=session.workdir,
            model=session.model,
            provider=session.provider,
            message_count=session.message_count,
            title=session.title,
        )
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def load_session(session_id: str, app: App = Depends(get_app)):
    """Load a session by ID (sets as current)."""
    session = app.load_session(session_id)
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
        title=session.title,
        messages=session.messages,
    )


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(session_id: str, app: App = Depends(get_app)):
    """Delete a session."""
    if not app.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return MessageResponse(ok=True)


@router.get("/history", response_model=HistoryResponse)
async def get_history(app: App = Depends(get_app)):
    """Get current message history."""
    return HistoryResponse(messages=app.messages)


@router.delete("/history", response_model=MessageResponse)
async def clear_history(app: App = Depends(get_app)):
    """Clear history (auto-saves first)."""
    app.clear_history_with_save()
    return MessageResponse(ok=True)
