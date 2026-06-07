"""Session — data, store protocol, file implementation, and manager."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .utils import read_json, write_json


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


class SessionStore(Protocol):
    """Session persistence protocol."""

    def list(self, workdir: str) -> list[Session]: ...
    def save(self, workdir: str, session: Session) -> None: ...
    def load(self, workdir: str, session_id: str) -> Session | None: ...
    def delete(self, workdir: str, session_id: str) -> bool: ...


def _hash_workdir(workdir: str) -> str:
    return hashlib.sha256(workdir.encode()).hexdigest()[:16]


class FileSessionStore:
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
        for f in d.glob("session_*.json"):
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


def _extract_title(messages: list[dict[str, Any]]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:80].replace("\n", " ").strip()
    return ""


def _text_content(content: Any) -> str:
    """Extract plain text from message content (handles str and list-of-parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    parts.append("[image attached]")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return str(content) if content else ""


def _short_args(arguments: str, max_len: int = 60) -> str:
    """Return a short display string for tool call arguments."""
    try:
        args = json.loads(arguments)
        if isinstance(args, dict):
            # Show first key-value pair as hint
            items = list(args.items())
            if items:
                k, v = items[0]
                v_str = str(v)
                if len(v_str) > 30:
                    v_str = v_str[:27] + "..."
                return f"{k}={v_str}"
        return arguments[:max_len]
    except (json.JSONDecodeError, TypeError):
        return arguments[:max_len]


def _render_session_md(session: Session, system_prompt: str = "") -> str:
    """Render a Session as a Markdown string."""
    lines: list[str] = []

    # --- YAML frontmatter ---
    lines.append("---")
    lines.append(f"session_id: {session.id}")
    lines.append(f"exported_at: {datetime.now().isoformat()}")
    lines.append(f"workdir: {session.workdir}")
    lines.append(f"message_count: {len(session.messages)}")
    if session.model:
        lines.append(f"model: {session.model}")
    if session.provider:
        lines.append(f"provider: {session.provider}")
    lines.append("---")
    lines.append("")

    # --- Title ---
    title = session.title or _extract_title(session.messages) or "Session"
    lines.append(f"# MoCode Session Export")
    lines.append("")

    # --- Overview ---
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Topic**: {title}")

    # Count turns and tool calls
    turn_count = 0
    tool_call_count = 0
    for msg in session.messages:
        if msg.get("role") == "user":
            turn_count += 1
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_call_count += len(msg["tool_calls"])
    lines.append(f"- **Conversation**: {turn_count} turns | {tool_call_count} tool calls")
    lines.append("")

    # --- System Prompt ---
    if system_prompt:
        lines.append("---")
        lines.append("")
        lines.append("## System Prompt")
        lines.append("")
        lines.append(system_prompt)
        lines.append("")

    # --- Turns ---
    turn_num = 0
    i = 0
    msgs = session.messages

    while i < len(msgs):
        msg = msgs[i]
        role = msg.get("role")

        if role == "user":
            turn_num += 1
            lines.append("---")
            lines.append("")
            lines.append(f"## Turn {turn_num}")
            lines.append("")

            lines.append("### User")
            lines.append("")
            lines.append(_text_content(msg.get("content", "")))
            lines.append("")

            # Now process all subsequent non-user messages belonging to this turn
            i += 1
            while i < len(msgs) and msgs[i].get("role") != "user":
                sub = msgs[i]
                sub_role = sub.get("role")

                if sub_role == "assistant":
                    reasoning = sub.get("reasoning_content", "")
                    content = _text_content(sub.get("content", ""))
                    tool_calls = sub.get("tool_calls", [])

                    lines.append("### Assistant")
                    lines.append("")

                    # Reasoning (thinking) in collapsible
                    if reasoning:
                        lines.append("<details><summary>Thinking</summary>")
                        lines.append("")
                        lines.append(reasoning)
                        lines.append("")
                        lines.append("</details>")
                        lines.append("")

                    # Text content
                    if content:
                        lines.append(content)
                        lines.append("")

                    # Tool calls
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tc_name = func.get("name", "unknown")
                        tc_args = func.get("arguments", "{}")
                        tc_id = tc.get("id", "")

                        lines.append(f"#### Tool Call: {tc_name} (`{_short_args(tc_args)}`)")
                        if tc_id:
                            lines.append(f"<!-- call_id: {tc_id} -->")
                        lines.append("```json")
                        # Pretty-print arguments if valid JSON
                        try:
                            parsed = json.loads(tc_args)
                            lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
                        except (json.JSONDecodeError, TypeError):
                            lines.append(tc_args)
                        lines.append("```")
                        lines.append("")

                    i += 1

                elif sub_role == "tool":
                    tc_id = sub.get("tool_call_id", "")
                    result_content = _text_content(sub.get("content", ""))

                    # Try to find the matching tool call name
                    tc_name = "Tool"
                    for prev_msg in msgs[:i]:
                        for prev_tc in prev_msg.get("tool_calls", []):
                            if prev_tc.get("id") == tc_id:
                                tc_name = prev_tc.get("function", {}).get("name", "Tool")
                                break

                    lines.append(f"<details><summary>Tool Result: {tc_name}</summary>")
                    lines.append("")
                    if tc_id:
                        lines.append(f"<!-- call_id: {tc_id} -->")
                    lines.append(result_content)
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")

                    i += 1

                else:
                    # Unknown role — skip
                    i += 1

        else:
            # Orphan non-user message at top level (shouldn't happen normally)
            i += 1

    return "\n".join(lines)


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
        md = _render_session_md(session, system_prompt)
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
            title = _extract_title(messages) or path.stem
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
