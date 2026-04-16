"""SessionManager — 接收 SessionStore 实例

v0.2 关键改进：SessionManager 不直接操作文件系统，而是通过 SessionStore 协议。
"""

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING

from .store import Session, SessionStore

if TYPE_CHECKING:
    pass


class SessionManager:
    """会话管理器 — 通过 Store 操作，按工作目录隔离"""

    def __init__(self, workdir: str, store: SessionStore):
        self._workdir = workdir
        self._workdir_hash = self._hash_workdir(workdir)
        self._store = store

    @staticmethod
    def _hash_workdir(workdir: str) -> str:
        return hashlib.sha256(workdir.encode()).hexdigest()[:16]

    def _generate_session_id(self) -> str:
        now = datetime.now()
        return now.strftime("session_%Y%m%d_%H%M%S")

    def list_sessions(self) -> list[Session]:
        return self._store.list(self._workdir_hash)

    def save_session(
        self,
        messages: list[dict],
        model: str = "",
        provider: str = "",
    ) -> Session:
        now = datetime.now().isoformat()
        session_id = self._generate_session_id()

        session = Session(
            id=session_id,
            created_at=now,
            updated_at=now,
            workdir=self._workdir,
            messages=messages.copy(),
            model=model,
            provider=provider,
        )

        self._store.save(self._workdir_hash, session)
        return session

    def load_session(self, session_id: str) -> Session | None:
        return self._store.load(self._workdir_hash, session_id)

    def delete_session(self, session_id: str) -> bool:
        return self._store.delete(self._workdir_hash, session_id)

    def update_session(
        self,
        session_id: str,
        messages: list[dict],
        model: str = "",
        provider: str = "",
    ) -> Session | None:
        session = self.load_session(session_id)
        if session is None:
            return None

        session.messages = messages.copy()
        session.updated_at = datetime.now().isoformat()
        if model:
            session.model = model
        if provider:
            session.provider = provider

        self._store.save(self._workdir_hash, session)
        return session
