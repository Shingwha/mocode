"""Session 管理模块

提供对话会话的持久化存储和恢复功能。
Session 按工作目录隔离，不同目录的 session 相互独立。
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..paths import SESSIONS_DIR


@dataclass
class Session:
    """会话数据结构

    Attributes:
        id: session ID，格式为 session_YYYYMMDD_HHMMSS
        created_at: 创建时间 (ISO format)
        updated_at: 更新时间 (ISO format)
        workdir: 原始工作目录路径
        messages: OpenAI format messages
        model: 使用的模型
        provider: 使用的供应商
    """

    id: str
    created_at: str
    updated_at: str
    workdir: str
    messages: list[dict[str, Any]]
    model: str = ""
    provider: str = ""

    @property
    def message_count(self) -> int:
        """消息数量"""
        return len(self.messages)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """从字典创建 Session"""
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
        """转换为字典"""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workdir": self.workdir,
            "messages": self.messages,
            "model": self.model,
            "provider": self.provider,
        }


class SessionManager:
    """会话管理器

    负责会话的 CRUD 操作，按工作目录隔离。
    文件存储结构:
        ~/.mocode/sessions/
            └── {workdir_hash}/
                ├── session_20260318_143022.json
                └── session_20260318_151530.json
    """

    def __init__(self, workdir: str):
        """初始化 SessionManager

        Args:
            workdir: 工作目录路径，用于 session 隔离
        """
        self._workdir = workdir
        self._workdir_hash = self._hash_workdir(workdir)
        self._sessions_dir = SESSIONS_DIR / self._workdir_hash

    @staticmethod
    def _hash_workdir(workdir: str) -> str:
        """生成工作目录的哈希值

        使用 SHA256 的前 16 位作为目录名，避免路径过长和特殊字符问题。
        """
        return hashlib.sha256(workdir.encode()).hexdigest()[:16]

    def get_sessions_dir(self) -> Path:
        """获取当前工作目录的 sessions 目录"""
        return self._sessions_dir

    def _ensure_sessions_dir(self) -> None:
        """确保 sessions 目录存在"""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def _generate_session_id(self) -> str:
        """生成 session ID

        格式: session_YYYYMMDD_HHMMSS
        """
        now = datetime.now()
        return now.strftime("session_%Y%m%d_%H%M%S")

    def _session_file_path(self, session_id: str) -> Path:
        """获取 session 文件路径"""
        return self._sessions_dir / f"{session_id}.json"

    def list_sessions(self) -> list[Session]:
        """列出当前工作目录的所有 session

        Returns:
            Session 列表，按更新时间降序排列（最新的在前）
        """
        if not self._sessions_dir.exists():
            return []

        sessions = []
        for file_path in self._sessions_dir.glob("session_*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sessions.append(Session.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                # 跳过损坏的 session 文件
                continue

        # 按更新时间降序排列
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def save_session(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        provider: str = "",
    ) -> Session:
        """保存当前对话为 session

        Args:
            messages: 对话消息列表
            model: 当前使用的模型
            provider: 当前使用的供应商

        Returns:
            保存的 Session 对象
        """
        self._ensure_sessions_dir()

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

        file_path = self._session_file_path(session_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)

        return session

    def load_session(self, session_id: str) -> Session | None:
        """加载指定 session

        Args:
            session_id: session ID

        Returns:
            Session 对象，如果不存在则返回 None
        """
        file_path = self._session_file_path(session_id)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def delete_session(self, session_id: str) -> bool:
        """删除指定 session

        Args:
            session_id: session ID

        Returns:
            是否删除成功
        """
        file_path = self._session_file_path(session_id)
        if not file_path.exists():
            return False

        try:
            file_path.unlink()
            return True
        except OSError:
            return False

    def update_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        model: str = "",
        provider: str = "",
    ) -> Session | None:
        """更新指定 session

        Args:
            session_id: session ID
            messages: 新的对话消息列表
            model: 当前使用的模型
            provider: 当前使用的供应商

        Returns:
            更新后的 Session 对象，如果不存在则返回 None
        """
        session = self.load_session(session_id)
        if session is None:
            return None

        session.messages = messages.copy()
        session.updated_at = datetime.now().isoformat()
        if model:
            session.model = model
        if provider:
            session.provider = provider

        file_path = self._session_file_path(session_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)

        return session
