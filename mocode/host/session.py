"""Session — the persisted record of a conversation, and the store that holds it.

A :class:`Session` is data: what was said, where, with which model. Nothing here
knows about a live conversation — :class:`~mocode.host.conversation.Conversation`
owns one of these identities while it runs and writes it out on demand.

Sessions are partitioned on disk by working directory, so two projects never mix
in a listing::

    ~/.mocode/sessions/<sha256(workdir)[:16]>/<session_id>.json

``SessionStore.list(workdir)`` serves one project; ``list_all()`` spans all of
them (each record carries its own ``workdir``, which is what lets a UI group by
project), and ``find(session_id)`` answers a deep link without knowing the
project first.
"""

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
    #: The system prompt the session ran with. A resume reinstates it
    #: byte-identical so the provider's prefix cache survives; empty for
    #: sessions recorded before prompts were frozen.
    system_prompt: str = ""
    #: The tool interface the session ran with — the other frozen half of the
    #: request, reinstated on resume for the same reason. Empty for sessions
    #: recorded before interfaces were frozen.
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    #: Each plugin's own state for this conversation, keyed by plugin name.
    #: The host owns the persistence (it travels with the session); the plugin
    #: owns the contents (``ctx.plugin_state(name)``). This is what lets a
    #: plugin remember anything across a save/resume — a baseline, a counter,
    #: an index — without the host knowing any plugin's shape.
    plugin_state: dict[str, dict[str, Any]] = field(default_factory=dict)
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
            system_prompt=data.get("system_prompt", ""),
            tool_schemas=data.get("tool_schemas", []),
            plugin_state=data.get("plugin_state", {}),
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
            "system_prompt": self.system_prompt,
            "tool_schemas": self.tool_schemas,
            "plugin_state": self.plugin_state,
            "metadata": self.metadata,
        }


def _hash_workdir(workdir: str) -> str:
    return hashlib.sha256(workdir.encode()).hexdigest()[:16]


def new_session_id() -> str:
    """An identity for a conversation, before anything is written to disk."""
    return f"session_{uuid4().hex[:12]}"


def timestamp() -> str:
    return datetime.now().isoformat()


def extract_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:80].replace("\n", " ").strip()
    return ""


class SessionStore:
    """File-based session storage. base_dir is parameterized, no paths.py dependency."""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or Path.home() / ".mocode" / "sessions"

    def _sessions_dir(self, workdir_hash: str) -> Path:
        d = self._base_dir / workdir_hash
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, workdir: str) -> list[Session]:
        """Every session recorded for one working directory, newest first."""
        return self._load_dir(self._base_dir / _hash_workdir(workdir))

    def list_all(self) -> list[Session]:
        """Every session in the store, across projects, newest first."""
        if not self._base_dir.exists():
            return []
        sessions: list[Session] = []
        for directory in self._base_dir.iterdir():
            if directory.is_dir():
                sessions.extend(self._load_dir(directory))
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def find(self, session_id: str) -> Session | None:
        """Look a session up by id alone — a link does not carry a project."""
        if not self._base_dir.exists():
            return None
        for path in self._base_dir.glob(f"*/{session_id}.json"):
            data = read_json(path)
            if data is None:
                continue
            try:
                return Session.from_dict(data)
            except KeyError:
                continue
        return None

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

    def _load_dir(self, directory: Path) -> list[Session]:
        if not directory.exists():
            return []
        sessions = []
        for f in directory.glob("*.json"):
            data = read_json(f)
            if data is None:
                continue
            try:
                sessions.append(Session.from_dict(data))
            except KeyError:
                continue
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions


# ── Portability ─────────────────────────────────────────────


def load_session_file(path: Path) -> tuple[list[dict], str] | None:
    """Read messages and title back out of of an exported file, or ``None``."""
    if not path.exists() or path.suffix != ".json":
        return None
    data = read_json(path)
    if isinstance(data, dict) and "messages" in data:
        messages = data["messages"]
        title = extract_title(messages) or path.stem
        return messages, title
    return None


__all__ = [
    "Session",
    "SessionStore",
    "extract_title",
    "load_session_file",
    "new_session_id",
    "timestamp",
]
