"""Session Coordinator - Manages session state and coordinates save/update decisions"""

from dataclasses import dataclass
from typing import Any

from .session import Session, SessionManager


@dataclass
class SessionState:
    """Tracks current session state"""

    current_session_id: str | None = None
    has_unsaved_changes: bool = False


class SessionCoordinator:
    """Coordinates session management with agent state

    Manages the decision logic for when to save vs update sessions,
    tracking the current session ID, and coordinating with the agent's
    message history.
    """

    def __init__(self, session_manager: SessionManager):
        """Initialize session coordinator

        Args:
            session_manager: Session manager instance for persistence
        """
        self._session_manager = session_manager
        self._state = SessionState()

    @property
    def current_session_id(self) -> str | None:
        """Current session ID"""
        return self._state.current_session_id

    @property
    def has_unsaved_changes(self) -> bool:
        """Whether there are unsaved changes"""
        return self._state.has_unsaved_changes

    def list_sessions(self) -> list[Session]:
        """List all sessions for current workdir

        Returns:
            List of sessions sorted by update time (newest first)
        """
        return self._session_manager.list_sessions()

    def save_session(
        self,
        messages: list[dict[str, Any]],
        model: str,
        provider: str,
    ) -> Session:
        """Save messages as a session

        If there's a current session ID, updates it; otherwise creates new.

        Args:
            messages: Conversation messages
            model: Current model
            provider: Current provider

        Returns:
            Saved session
        """
        if self._state.current_session_id:
            # Try to update existing session
            session = self._session_manager.update_session(
                session_id=self._state.current_session_id,
                messages=messages,
                model=model,
                provider=provider,
            )
            if session:
                self._state.has_unsaved_changes = False
                return session
            # Fall through if update failed (session was deleted)

        # Create new session
        session = self._session_manager.save_session(
            messages=messages,
            model=model,
            provider=provider,
        )
        self._state.current_session_id = session.id
        self._state.has_unsaved_changes = False
        return session

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID

        Args:
            session_id: Session ID to load

        Returns:
            Session if found, None otherwise
        """
        session = self._session_manager.load_session(session_id)
        if session:
            self._state.current_session_id = session_id
            self._state.has_unsaved_changes = False
        return session

    def clear_state(self) -> None:
        """Clear session state (after clearing history)"""
        self._state.current_session_id = None
        self._state.has_unsaved_changes = False

    def mark_unsaved(self) -> None:
        """Mark that there are unsaved changes"""
        self._state.has_unsaved_changes = True

    def delete_session(self, session_id: str) -> bool:
        """Delete a session

        Args:
            session_id: Session ID to delete

        Returns:
            True if deleted successfully
        """
        result = self._session_manager.delete_session(session_id)
        if result and self._state.current_session_id == session_id:
            self._state.current_session_id = None
        return result
