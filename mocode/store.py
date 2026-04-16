"""ConfigStore + SessionStore Protocol + 文件/内存实现

持久化与数据分离：Config 只持有数据，Store 负责读写。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .paths import CONFIG_PATH, SESSIONS_DIR


# ---- Config Store ----


class ConfigStore(Protocol):
    """配置存储协议"""

    def load(self) -> dict | None: ...
    def save(self, data: dict) -> None: ...


class FileConfigStore:
    """从文件读写配置"""

    def __init__(self, path: Path | None = None):
        self._path = path or CONFIG_PATH

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, IOError):
            return None

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class InMemoryConfigStore:
    """内存存储，用于测试和 gateway"""

    def __init__(self, data: dict | None = None):
        self._data = data

    def load(self) -> dict | None:
        return self._data

    def save(self, data: dict) -> None:
        self._data = data


# ---- Session Store ----


@dataclass
class Session:
    """会话数据结构"""

    id: str
    created_at: str
    updated_at: str
    workdir: str
    messages: list[dict[str, Any]]
    model: str = ""
    provider: str = ""

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
            model=data.get("model", ""),
            provider=data.get("provider", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workdir": self.workdir,
            "messages": self.messages,
            "model": self.model,
            "provider": self.provider,
        }


class SessionStore(Protocol):
    """会话存储协议"""

    def list(self, workdir_hash: str) -> list[Session]: ...
    def save(self, workdir_hash: str, session: Session) -> None: ...
    def load(self, workdir_hash: str, session_id: str) -> Session | None: ...
    def delete(self, workdir_hash: str, session_id: str) -> bool: ...


class FileSessionStore:
    """文件系统会话存储"""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or SESSIONS_DIR

    def _sessions_dir(self, workdir_hash: str) -> Path:
        d = self._base_dir / workdir_hash
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list(self, workdir_hash: str) -> list[Session]:
        d = self._base_dir / workdir_hash
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

    def save(self, workdir_hash: str, session: Session) -> None:
        d = self._sessions_dir(workdir_hash)
        path = d / f"{session.id}.json"
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, workdir_hash: str, session_id: str) -> Session | None:
        path = self._base_dir / workdir_hash / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def delete(self, workdir_hash: str, session_id: str) -> bool:
        path = self._base_dir / workdir_hash / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False


class InMemorySessionStore:
    """内存会话存储，用于测试"""

    def __init__(self):
        self._data: dict[str, dict[str, Session]] = {}

    def list(self, workdir_hash: str) -> list[Session]:
        entries = self._data.get(workdir_hash, {})
        sessions = list(entries.values())
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save(self, workdir_hash: str, session: Session) -> None:
        if workdir_hash not in self._data:
            self._data[workdir_hash] = {}
        self._data[workdir_hash][session.id] = session

    def load(self, workdir_hash: str, session_id: str) -> Session | None:
        return self._data.get(workdir_hash, {}).get(session_id)

    def delete(self, workdir_hash: str, session_id: str) -> bool:
        entries = self._data.get(workdir_hash, {})
        if session_id in entries:
            del entries[session_id]
            return True
        return False
