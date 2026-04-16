"""Cron job persistence - one JSON file per job in ~/.mocode/cron/"""

import json
import logging
import os
from pathlib import Path

from .types import CronJob

logger = logging.getLogger(__name__)


class CronJobStore:
    def __init__(self, base_dir: Path):
        self._dir = base_dir

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def save(self, job: CronJob) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._path(job.id)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def load(self, job_id: str) -> CronJob | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return CronJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.error("Failed to load cron job %s: %s", job_id, e)
            return None

    def load_all(self) -> list[CronJob]:
        if not self._dir.exists():
            return []
        jobs = []
        for path in self._dir.glob("*.json"):
            try:
                jobs.append(CronJob.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception as e:
                logger.error("Failed to load cron job from %s: %s", path, e)
        return jobs

    def delete(self, job_id: str) -> bool:
        path = self._path(job_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_by_user(self, session_key: str) -> list[CronJob]:
        return [j for j in self.load_all() if j.user_session_key == session_key]
