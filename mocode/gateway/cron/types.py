"""Cron job data model"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class ScheduleMode(str, Enum):
    INTERVAL = "interval"
    CRON_EXPR = "cron_expr"
    ONE_SHOT = "one_shot"


@dataclass
class CronJob:
    name: str
    user_session_key: str
    channel: str
    chat_id: str
    prompt: str
    schedule_mode: ScheduleMode
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    interval_s: int = 0
    cron_expr: str = ""
    timezone: str = "UTC"
    deliver: bool = True
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    run_count: int = 0
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "user_session_key": self.user_session_key,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "prompt": self.prompt,
            "schedule_mode": self.schedule_mode.value,
            "interval_s": self.interval_s,
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "deliver": self.deliver,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "run_count": self.run_count,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CronJob":
        d = dict(d)
        d["schedule_mode"] = ScheduleMode(d.pop("schedule_mode"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
