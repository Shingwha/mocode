"""Session — data, store protocol, file implementation, and manager."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .io import read_json, write_json


@dataclass
class Session:
    """Conversation session — pure data, serializable."""

    id: str
    created_at: str
    updated_at: str
    workdir: str
    messages: list[dict[str, Any]]
    title: str = ""
    model: str = ""
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            workdir=data["workdir"],
            messages=data.get("messages", []),
            title=data.get("title", ""),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workdir": self.workdir,
            "messages": self.messages,
            "title": self.title,
            "model": self.model,
            "provider": self.provider,
            "metadata": self.metadata,
        }


def _hash_workdir(workdir: str) -> str:
    return hashlib.sha256(workdir.encode()).hexdigest()[:16]


class SessionStore:
    """File-based session storage. base_dir is parameterized, no paths.py dependency."""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or Path.home() / ".mocode" / "sessions"

    def _sessions_dir(self, workdir_hash: str) -> Path:
        d = self._base_dir / workdir_hash
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, workdir: str) -> list[Session]:
        d = self._base_dir / _hash_workdir(workdir)
        if not d.exists():
            return []
        sessions = []
        for f in d.glob("*.json"):
            data = read_json(f)
            if data is not None:
                try:
                    sessions.append(Session.from_dict(data))
                except KeyError:
                    continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save(self, workdir: str, session: Session) -> None:
        d = self._sessions_dir(_hash_workdir(workdir))
        write_json(d / f"{session.id}.json", session.to_dict())

    def load(self, workdir: str, session_id: str) -> Session | None:
        data = read_json(
            self._base_dir / _hash_workdir(workdir) / f"{session_id}.json"
        )
        if data is None:
            return None
        try:
            return Session.from_dict(data)
        except KeyError:
            return None

    def delete(self, workdir: str, session_id: str) -> bool:
        path = self._base_dir / _hash_workdir(workdir) / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False


def extract_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:80].replace("\n", " ").strip()
    return ""


class SessionManager:
    """Orchestrates session lifecycle — create, resume, save, import, export."""

    def __init__(self, workdir: str, store: SessionStore):
        self._workdir = workdir
        self._store = store
        self._active_id: str | None = None

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def create(self, metadata: dict[str, Any] | None = None) -> str:
        session_id = f"session_{uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        session = Session(
            id=session_id,
            created_at=now,
            updated_at=now,
            workdir=self._workdir,
            messages=[],
            metadata=metadata or {},
        )
        self._store.save(self._workdir, session)
        self._active_id = session_id
        return session_id

    def resume(self, session_id: str) -> Session | None:
        session = self._store.load(self._workdir, session_id)
        if session is None:
            return None
        self._active_id = session_id
        return session

    def switch_to(self, session: Session) -> None:
        """Set an already-loaded session as active (used by resume)."""
        self._active_id = session.id

    def get_active(self) -> Session | None:
        """Return the currently active session, or None."""
        if self._active_id is None:
            return None
        return self._store.load(self._workdir, self._active_id)

    def save(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        provider: str = "",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        now = datetime.now().isoformat()
        if title is None:
            title = extract_title(messages)

        if self._active_id:
            session = self._store.load(self._workdir, self._active_id)
            if session:
                session.messages = messages.copy()
                session.updated_at = now
                session.model = model or session.model
                session.provider = provider or session.provider
                if title:
                    session.title = title
                if metadata:
                    session.metadata.update(metadata)
                self._store.save(self._workdir, session)
                return session

        session_id = f"session_{uuid4().hex[:12]}"
        session = Session(
            id=session_id,
            created_at=now,
            updated_at=now,
            workdir=self._workdir,
            messages=messages.copy(),
            title=title,
            model=model,
            provider=provider,
            metadata=metadata or {},
        )
        self._store.save(self._workdir, session)
        self._active_id = session_id
        return session

    def export_to_file(
        self, session: Session, path: Path, system_prompt: str = ""
    ) -> None:
        """Export session to a portable JSON file."""
        data = {"system_prompt": system_prompt, **session.to_dict()}
        write_json(path, data)

    def export_to_md(
        self, session: Session, path: Path, system_prompt: str = ""
    ) -> None:
        """Export session to a human-readable Markdown file."""
        from .export import render_session_md  # lazy import

        md = render_session_md(session, system_prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")

    @staticmethod
    def import_from_file(path: Path) -> tuple[list[dict], str] | None:
        """Import messages from a portable JSON file. Returns (messages, title) or None."""
        if not path.exists() or path.suffix != ".json":
            return None
        data = read_json(path)
        if isinstance(data, dict) and "messages" in data:
            messages = data["messages"]
            title = extract_title(messages) or path.stem
            return messages, title
        return None

    def list(self) -> list[Session]:
        return self._store.list(self._workdir)

    def delete(self, session_id: str) -> bool:
        result = self._store.delete(self._workdir, session_id)
        if result and self._active_id == session_id:
            self._active_id = None
        return result

    def clear(self) -> None:
        self._active_id = None
