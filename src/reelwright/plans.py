"""Plans and quotas.

Plans are plain configuration. By default (``REELWRIGHT_ENFORCE_PLANS=false``) nothing is limited:
every organization behaves like the unlimited ``selfhost`` plan. Operators who run a shared instance can
turn enforcement on and edit :data:`PLANS` (or assign plans per organization through the admin API).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import utcnow
from .models import Job, Membership, Organization, UsageEvent


@dataclass(frozen=True)
class Plan:
    name: str
    monthly_minutes: int  # minutes of source video per calendar month; 0 = unlimited
    max_upload_bytes: int  # 0 = use the instance-wide limit
    max_concurrent_jobs: int  # 0 = unlimited
    max_clips_per_job: int
    max_seats: int  # 0 = unlimited


GB = 1_000_000_000
PLANS: dict[str, Plan] = {
    "selfhost": Plan("selfhost", 0, 0, 0, 30, 0),
    "free": Plan("free", 60, GB // 2, 1, 5, 1),
    "pro": Plan("pro", 1200, 2 * GB, 3, 10, 1),
    "team": Plan("team", 6000, 4 * GB, 6, 15, 10),
    "enterprise": Plan("enterprise", 0, 0, 20, 30, 0),
}


class PlanLimitError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def effective_plan(settings: Settings, org: Organization) -> Plan:
    if not settings.enforce_plans:
        return PLANS["selfhost"]
    return PLANS.get(org.plan, PLANS["free"])


def month_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def minutes_used(db: Session, org_id: str, now: datetime | None = None) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0.0)).where(
            UsageEvent.org_id == org_id,
            UsageEvent.kind == "source_minutes",
            UsageEvent.created_at >= month_start(now),
        )
    )
    return float(total or 0.0)


def usage_summary(db: Session, settings: Settings, org: Organization) -> dict:
    plan = effective_plan(settings, org)
    used = minutes_used(db, org.id)
    return {
        "plan": plan.name,
        "enforced": settings.enforce_plans,
        "minutes_used": round(used, 2),
        "minutes_limit": plan.monthly_minutes,
        "minutes_remaining": None if plan.monthly_minutes == 0 else round(max(0.0, plan.monthly_minutes - used), 2),
        "max_concurrent_jobs": plan.max_concurrent_jobs,
        "max_clips_per_job": plan.max_clips_per_job,
        "max_seats": plan.max_seats,
    }


def check_can_submit(db: Session, settings: Settings, org: Organization, *, upload_bytes: int = 0) -> Plan:
    """Raise :class:`PlanLimitError` when a new job must be refused; return the effective plan otherwise."""
    plan = effective_plan(settings, org)
    limit_bytes = plan.max_upload_bytes or settings.max_upload_bytes
    if upload_bytes and upload_bytes > min(limit_bytes, settings.max_upload_bytes):
        raise PlanLimitError("upload_too_large", f"File exceeds the {min(limit_bytes, settings.max_upload_bytes)} byte limit.")
    if plan.monthly_minutes and minutes_used(db, org.id) >= plan.monthly_minutes:
        raise PlanLimitError("minutes_exhausted", "The monthly source-minute allowance is used up.")
    if plan.max_concurrent_jobs:
        active = db.scalar(
            select(func.count(Job.id)).where(Job.org_id == org.id, Job.status.in_(("queued", "running")))
        )
        if (active or 0) >= plan.max_concurrent_jobs:
            raise PlanLimitError("too_many_jobs", "Too many jobs are already queued or running for this organization.")
    return plan


def check_can_add_seat(db: Session, settings: Settings, org: Organization) -> None:
    plan = effective_plan(settings, org)
    if plan.max_seats:
        seats = db.scalar(select(func.count(Membership.id)).where(Membership.org_id == org.id)) or 0
        if seats >= plan.max_seats:
            raise PlanLimitError("seat_limit", f"The {plan.name} plan allows {plan.max_seats} seat(s).")


def record_usage(db: Session, org_id: str, job_id: str, source_seconds: float) -> None:
    db.add(UsageEvent(org_id=org_id, job_id=job_id, kind="source_minutes", quantity=source_seconds / 60.0))
