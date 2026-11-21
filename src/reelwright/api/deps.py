"""Authentication, authorization and request helpers."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_sessionmaker, utcnow
from ..models import ROLES, ApiKey, AuditLog, Membership, Organization, User
from ..security import API_KEY_PREFIX, decode_token, hash_api_key

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class Principal:
    user: User
    org: Organization
    role: str
    via: str  # "token" | "api_key"
    api_key_id: str | None = None

    def require(self, minimum: str) -> None:
        if ROLES.index(self.role) < ROLES.index(minimum):
            raise HTTPException(403, f"This action needs the '{minimum}' role.")


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def get_principal(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_org_id: str | None = Header(default=None),
) -> Principal:
    settings = get_settings()
    bearer = _bearer(authorization)
    raw_key = x_api_key or (bearer if bearer and bearer.startswith(API_KEY_PREFIX) else None)
    if raw_key:
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_key)))
        if key is None or key.revoked_at is not None:
            raise HTTPException(401, "Invalid API key.")
        user, org = db.get(User, key.user_id), db.get(Organization, key.org_id)
        member = db.scalar(select(Membership).where(Membership.user_id == key.user_id, Membership.org_id == key.org_id))
        if user is None or user.disabled or org is None or member is None:
            raise HTTPException(401, "Invalid API key.")
        if key.last_used_at is None or (utcnow() - key.last_used_at).total_seconds() > 60:
            key.last_used_at = utcnow()
        return Principal(user, org, "member" if ROLES.index(member.role) >= 1 else member.role, "api_key", key.id)
    if not bearer:
        raise HTTPException(401, "Authentication required.")
    user_id = decode_token(settings.secret_key, bearer)
    user = db.get(User, user_id) if user_id else None
    if user is None or user.disabled:
        raise HTTPException(401, "Invalid or expired token.")
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)).all()
    if not memberships:
        raise HTTPException(403, "This account belongs to no organization.")
    chosen = None
    if x_org_id:
        chosen = next((m for m in memberships if m.org_id == x_org_id), None)
        if chosen is None:
            raise HTTPException(403, "You are not a member of that organization.")
    else:
        personal = {o.id for o in db.scalars(select(Organization).where(Organization.id.in_([m.org_id for m in memberships]), Organization.personal.is_(True)))}
        chosen = next((m for m in memberships if m.org_id in personal), memberships[0])
    return Principal(user, db.get(Organization, chosen.org_id), chosen.role, "token")


def require_instance_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.user.is_admin:
        raise HTTPException(403, "Instance administrator access required.")
    return principal


def audit(db: Session, principal: Principal | None, action: str, detail: dict | None = None, org_id: str | None = None) -> None:
    db.add(AuditLog(org_id=org_id or (principal.org.id if principal else None), user_id=principal.user.id if principal else None, action=action, detail=detail or {}))


class RateLimiter:
    """Small in-memory sliding-window limiter (per process). Put a reverse proxy in front for stronger limits."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(429, "Too many attempts. Try again in a minute.")
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter(limit=8, window=60.0)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
