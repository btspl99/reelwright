"""A database-backed job queue (no Redis needed). Safe with several worker threads or processes."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update

from .config import Settings
from .db import session_scope, utcnow
from .models import Job, Organization
from .plans import effective_plan


def claim_next(settings: Settings) -> str | None:
    """Atomically move the oldest eligible queued job to ``running`` and return its id."""
    with session_scope() as db:
        candidates = db.execute(
            select(Job.id, Job.org_id).where(Job.status == "queued").order_by(Job.created_at).limit(25)
        ).all()
        for job_id, org_id in candidates:
            if settings.enforce_plans:
                org = db.get(Organization, org_id)
                limit = effective_plan(settings, org).max_concurrent_jobs if org else 0
                if limit:
                    running = db.scalar(select(func.count(Job.id)).where(Job.org_id == org_id, Job.status == "running")) or 0
                    if running >= limit:
                        continue
            now = utcnow()
            claimed = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="running", started_at=now, heartbeat_at=now, attempts=Job.attempts + 1, stage="starting")
            )
            if claimed.rowcount == 1:
                return job_id
    return None


def requeue_stale(settings: Settings, max_attempts: int = 3) -> int:
    """Return jobs whose worker vanished (no heartbeat) to the queue, or fail them after ``max_attempts``."""
    cutoff = utcnow() - timedelta(seconds=settings.stale_job_seconds)
    changed = 0
    with session_scope() as db:
        stale = db.scalars(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).all()
        for job in stale:
            if job.attempts >= max_attempts:
                job.status, job.error, job.finished_at = "failed", "The worker stopped responding repeatedly.", utcnow()
            else:
                job.status, job.stage, job.progress = "queued", "queued", 0.0
            changed += 1
    return changed
