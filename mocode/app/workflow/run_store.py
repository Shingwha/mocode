"""WorkflowRunStore — persistence for workflow run records."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..utils import read_json, write_json
from .models import NodeResult


class WorkflowRunStore:
    """File-based store for workflow run records.

    Each run lives in its own directory: ``~/.mocode/runs/<run_id>/run.json``.
    Auxiliary files (board.md, task results) coexist in the same directory.
    """

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or Path.home() / ".mocode" / "runs"

    def _path(self, run_id: str) -> Path:
        return self._base_dir / run_id / "run.json"

    def _ensure_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        """Return the directory for a run (may not exist yet)."""
        return self._base_dir / run_id

    def ensure_run_dir(self, run_id: str) -> Path:
        """Create and return the run directory."""
        d = self.run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def board_path(self, run_id: str) -> Path:
        """Return the path to ``board.md`` inside the run directory."""
        return self.run_dir(run_id) / "board.md"

    def task_path(self, run_id: str, task_id: str) -> Path:
        """Return the path to ``tasks/<task_id>.md`` inside the run directory."""
        return self.run_dir(run_id) / "tasks" / f"{task_id}.md"

    # ── Create / Write ─────────────────────────────────────────

    def create(
        self,
        workflow_name: str,
        workflow_path: str,
        args: dict[str, str] | None = None,
    ) -> str:
        """Generate a new run_id, write initial ``running`` record, return run_id."""
        run_id = f"wf_{uuid4().hex[:12]}"
        record: dict[str, Any] = {
            "run_id": run_id,
            "pid": None,
            "workflow_name": workflow_name,
            "workflow_path": workflow_path,
            "status": "running",
            "args": args or {},
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "wall_duration": None,
            "results": [],
        }
        self._ensure_dir()
        self._write(run_id, record)
        return run_id

    def update_pid(self, run_id: str, pid: int) -> None:
        """Write the child process PID into the record."""
        record = self.load(run_id)
        if record is None:
            return
        record["pid"] = pid
        self._write(run_id, record)

    def update(
        self,
        run_id: str,
        results: list[NodeResult],
        status: str,
        finished_at: str | None = None,
    ) -> None:
        """Rewrite the record with updated results and status."""
        record = self.load(run_id)
        if record is None:
            return
        record["status"] = status
        record["results"] = [asdict(r) for r in results]
        if finished_at is not None:
            record["finished_at"] = finished_at
        if record.get("started_at") and finished_at:
            try:
                started = datetime.fromisoformat(record["started_at"])
                finished = datetime.fromisoformat(finished_at)
                record["wall_duration"] = (finished - started).total_seconds()
            except (ValueError, TypeError):
                pass
        self._write(run_id, record)

    # ── Read ────────────────────────────────────────────────────

    def load(self, run_id: str) -> dict[str, Any] | None:
        """Read and parse the run JSON. Returns None if not found."""
        return read_json(self._path(run_id))

    def is_alive(self, run_id: str) -> bool:
        """Check if the recorded PID process still exists."""
        record = self.load(run_id)
        if record is None:
            return False
        pid = record.get("pid")
        if pid is None:
            return False
        return _pid_exists(pid)

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent runs sorted by started_at descending."""
        self._ensure_dir()
        records: list[dict[str, Any]] = []
        for d in self._base_dir.glob("wf_*"):
            if not d.is_dir():
                continue
            data = read_json(d / "run.json")
            if data is not None:
                records.append(data)
        records.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return records[:limit]

    def find_latest(self, workflow_name: str | None = None) -> dict[str, Any] | None:
        """Find the most recent run, optionally filtered by workflow name."""
        for record in self.list_recent(limit=100):
            if workflow_name is None or record.get("workflow_name") == workflow_name:
                return record
        return None

    def resolve_run_id(self, run_id: str | None) -> str | None:
        """Resolve a run_id: return as-is if given, otherwise find latest."""
        if run_id:
            return run_id
        record = self.find_latest()
        return record["run_id"] if record else None

    # ── Internal ────────────────────────────────────────────────

    def _write(self, run_id: str, record: dict[str, Any]) -> None:
        self._ensure_dir()
        write_json(self._path(run_id), record)


def _pid_exists(pid: int) -> bool:
    """Cross-platform process liveness check."""
    if sys.platform == "win32":
        return _pid_exists_windows(pid)
    return _pid_exists_posix(pid)


def _pid_exists_posix(pid: int) -> bool:
    """POSIX: os.kill(pid, 0) — alive if no error or PermissionError."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we can't signal it
    except OSError:
        return False
    return True


def _pid_exists_windows(pid: int) -> bool:
    """Windows: use ctypes OpenProcess to check liveness."""
    import ctypes

    # SYNCHRONIZE access right (0x100000) — sufficient for OpenProcess
    handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False
