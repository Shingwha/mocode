"""Session — self-contained session module

Session dataclass, SessionStore protocol, file/memory implementations,
and SessionManager with lifecycle management.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .message_utils import sanitize_messages
from .paths import SESSIONS_DIR


@dataclass
class Session:
    id: str
    created_at: str
    updated_at: str
    workdir: str
    messages: list[dict[str, Any]]
    title: str = ""
    model: str = ""
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def message_count(self) -> int:
        return len(self.messages)

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


class SessionStore(Protocol):
    def list(self, workdir: str) -> list[Session]: ...
    def save(self, workdir: str, session: Session) -> None: ...
    def load(self, workdir: str, session_id: str) -> Session | None: ...
    def delete(self, workdir: str, session_id: str) -> bool: ...


def _hash_workdir(workdir: str) -> str:
    return hashlib.sha256(workdir.encode()).hexdigest()[:16]


class FileSessionStore:
    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or SESSIONS_DIR

    def _sessions_dir(self, workdir_hash: str) -> Path:
        d = self._base_dir / workdir_hash
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, workdir: str) -> list[Session]:
        d = self._base_dir / _hash_workdir(workdir)
        if not d.exists():
            return []
        sessions = []
        for f in d.glob("session_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append(Session.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save(self, workdir: str, session: Session) -> None:
        d = self._sessions_dir(_hash_workdir(workdir))
        path = d / f"{session.id}.json"
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, workdir: str, session_id: str) -> Session | None:
        path = self._base_dir / _hash_workdir(workdir) / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError):
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


class InMemorySessionStore:
    def __init__(self):
        self._data: dict[str, dict[str, Session]] = {}

    def list(self, workdir: str) -> list[Session]:
        entries = self._data.get(_hash_workdir(workdir), {})
        sessions = list(entries.values())
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save(self, workdir: str, session: Session) -> None:
        key = _hash_workdir(workdir)
        if key not in self._data:
            self._data[key] = {}
        self._data[key][session.id] = session

    def load(self, workdir: str, session_id: str) -> Session | None:
        return self._data.get(_hash_workdir(workdir), {}).get(session_id)

    def delete(self, workdir: str, session_id: str) -> bool:
        entries = self._data.get(_hash_workdir(workdir), {})
        if session_id in entries:
            del entries[session_id]
            return True
        return False


def _extract_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:80].replace("\n", " ").strip()
    return ""


class SessionManager:
    def __init__(self, workdir: str, store: SessionStore):
        self._workdir = workdir
        self._store = store
        self._active_id: str | None = None
        self._dirty: bool = False

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def active_id(self) -> str | None:
        return self._active_id

    @property
    def is_dirty(self) -> bool:
        return self._dirty

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
        self._dirty = False
        return session_id

    def resume(self, session_id: str) -> Session | None:
        session = self._store.load(self._workdir, session_id)
        if session is None:
            return None
        session.messages = sanitize_messages(session.messages)
        self._active_id = session_id
        self._dirty = False
        return session

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
            title = _extract_title(messages)

        if self._active_id:
            session = self._store.load(self._workdir, self._active_id)
            if session:
                session.messages = messages.copy()
                session.updated_at = now
                session.model = model or session.model
                session.provider = provider or session.provider
                if title:
                    session.title = title
                self._store.save(self._workdir, session)
                self._dirty = False
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
        self._dirty = False
        return session

    def save_if_dirty(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        provider: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Session | None:
        if not self._dirty:
            return None
        return self.save(messages, model, provider, metadata=metadata)

    def list(
        self,
        source: str | None = None,
        channel: str | None = None,
    ) -> list[Session]:
        sessions = self._store.list(self._workdir)
        if source is not None:
            sessions = [s for s in sessions if s.metadata.get("source") == source]
        if channel is not None:
            sessions = [s for s in sessions if s.metadata.get("channel") == channel]
        return sessions

    def delete(self, session_id: str) -> bool:
        result = self._store.delete(self._workdir, session_id)
        if result and self._active_id == session_id:
            self._active_id = None
        return result

    def clear(self) -> None:
        self._active_id = None
        self._dirty = False

    def invalidate(self) -> None:
        self._active_id = None
        self._dirty = True

    def mark_dirty(self) -> None:
        self._dirty = True
