"""Cron scheduler - async tick loop that fires jobs on schedule"""

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from ..bus import MessageBus, OutboundMessage
from ..router import UserRouter
from ..tools import ChatContext, chat_session
from .store import CronJobStore
from .types import CronJob, ScheduleMode

logger = logging.getLogger(__name__)


class CronScheduler:
    def __init__(
        self,
        store: CronJobStore,
        router: UserRouter,
        bus: MessageBus,
        tick_interval_s: float = 1.0,
    ):
        self._store = store
        self._router = router
        self._bus = bus
        self._tick_interval_s = tick_interval_s
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # Rebuild next_run_at for any jobs that need it
        for job in self._store.load_all():
            if job.enabled and job.next_run_at <= 0:
                job.next_run_at = self.compute_next_run(job)
                self._store.save(job)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Cron scheduler started (tick=%.1fs)", self._tick_interval_s)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def compute_next_run(self, job: CronJob) -> float:
        now = time.time()
        if job.schedule_mode == ScheduleMode.INTERVAL:
            return now + job.interval_s
        elif job.schedule_mode == ScheduleMode.CRON_EXPR:
            tz = ZoneInfo(job.timezone)
            dt = datetime.fromtimestamp(now, tz=tz)
            cron = croniter(job.cron_expr, dt)
            return cron.get_next(datetime).timestamp()
        elif job.schedule_mode == ScheduleMode.ONE_SHOT:
            if job.interval_s > 0:
                return now + job.interval_s
            return now
        return now

    def create_job(self, job: CronJob) -> None:
        """Save a new job (next_run_at should already be set)."""
        self._store.save(job)

    def list_jobs(self, session_key: str) -> list[CronJob]:
        """List all jobs belonging to a user session."""
        return self._store.list_by_user(session_key)

    def delete_job(self, job_id: str) -> bool:
        """Delete a job by ID. Returns True if deleted."""
        return self._store.delete(job_id)

    def get_job(self, job_id: str) -> CronJob | None:
        """Load a single job by ID."""
        return self._store.load(job_id)

    async def _run_loop(self) -> None:
        while True:
            try:
                now = time.time()
                for job in self._store.load_all():
                    if not job.enabled or now < job.next_run_at:
                        continue
                    job.last_run_at = now
                    job.run_count += 1
                    if job.schedule_mode == ScheduleMode.ONE_SHOT:
                        job.enabled = False
                    else:
                        job.next_run_at = self.compute_next_run(job)
                    self._store.save(job)
                    asyncio.create_task(self._fire_job(job))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Cron tick error: %s", e)
            await asyncio.sleep(self._tick_interval_s)

    @staticmethod
    def _build_cron_prompt(job: CronJob) -> str:
        parts = [
            "[定时任务触发]",
            f"任务名称: {job.name}",
            f"任务ID: {job.id}",
        ]
        if job.run_count > 1:
            parts.append(f"第 {job.run_count} 次执行")
        parts.append("")
        parts.append(
            "这是一条由用户预设的定时任务，已按计划自动触发。"
            "请直接执行以下指令，你的回复将直接发送给用户，"
            "用自然、友好的语气与用户对话，不要提及这是定时任务或系统触发。"
        )
        parts.append("")
        parts.append(job.prompt)
        return "\n".join(parts)

    async def _fire_job(self, job: CronJob) -> None:
        logger.info("Firing cron job %s (%s)", job.id, job.name)
        try:
            session = self._router.get_or_create(job.user_session_key)
            async with session.lock:
                async with chat_session(ChatContext(
                    core=session.core,
                    scheduler=self,
                    session_key=job.user_session_key,
                    channel=job.channel,
                    chat_id=job.chat_id,
                )) as pending:
                    prompt = self._build_cron_prompt(job)
                    response = await session.core.chat(prompt)

                if job.deliver and response:
                    await self._bus.publish_outbound(OutboundMessage(
                        channel=job.channel,
                        chat_id=job.chat_id,
                        content=response,
                    ))
                for media_path in pending.paths:
                    await self._bus.publish_outbound(OutboundMessage(
                        channel=job.channel,
                        chat_id=job.chat_id,
                        content="",
                        media=[media_path],
                    ))
        except Exception as e:
            logger.error("Error firing cron job %s: %s", job.id, e)
        finally:
            if not job.enabled:
                self._store.delete(job.id)
                logger.info("Cleaned up one-shot job %s", job.id)
