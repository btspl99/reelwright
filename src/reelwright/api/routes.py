"""REST routes."""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import (
    ROLES,
    ApiKey,
    AuditLog,
    Clip,
    Job,
    Membership,
    Organization,
    UsageEvent,
    User,
    Webhook,
    WebhookDelivery,
    new_id,
)
from ..options import JobOptions, RerenderOptions
from ..pipeline.ingest import IngestError, store_upload
from ..pipeline.urls import UnsafeURLError, validate_source_url
from ..plans import PLANS, PlanLimitError, check_can_add_seat, check_can_submit, usage_summary
from ..security import create_token, generate_api_key, hash_password, verify_media, verify_password
from ..webhooks import EVENTS
from .deps import (
    EMAIL_RE,
    Principal,
    audit,
    client_ip,
    get_db,
    get_principal,
    login_limiter,
    require_instance_admin,
)
from .serializers import clip_dict, iso, job_dict, key_dict, org_dict, user_dict

router = APIRouter(prefix="/api")
media_router = APIRouter()


def _limit_error(exc: PlanLimitError) -> HTTPException:
    return HTTPException(402, {"code": exc.code, "message": exc.message})


# ----------------------------------------------------------------------------- auth
class Credentials(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=256)
    name: str = Field("", max_length=120)


@router.post("/auth/register", status_code=201)
def register(body: Credentials, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    login_limiter.check(f"register:{client_ip(request)}")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.") from None
    if len(body.password) < 10:
        raise HTTPException(422, "The password must be at least 10 characters.") from None
    first = (db.scalar(select(func.count(User.id))) or 0) == 0
    if not first and not settings.registration_open:
        raise HTTPException(403, "Registration is closed on this instance.") from None
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists.") from None
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), is_admin=first)
    db.add(user)
    db.flush()
    org = Organization(name=(body.name.strip() or email.split("@")[0]) + "'s workspace", plan="free", personal=True)
    db.add(org)
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    audit(db, None, "user.register", {"user_id": user.id}, org_id=org.id)
    token = create_token(settings.secret_key, user.id, settings.token_ttl_hours)
    return {"token": token, "user": user_dict(user), "org": org_dict(org, "owner")}


@router.post("/auth/login")
def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    email = body.email.strip().lower()
    login_limiter.check(f"login:{client_ip(request)}:{email}")
    user = db.scalar(select(User).where(User.email == email))
    # Always run a hash comparison so response time does not reveal whether the email exists.
    ok = verify_password(body.password, user.password_hash if user else hash_password("x" * 12))
    if not user or not ok or user.disabled:
        raise HTTPException(401, "Wrong email or password.") from None
    return {"token": create_token(settings.secret_key, user.id, settings.token_ttl_hours), "user": user_dict(user)}


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Organization, Membership.role).join(Membership, Membership.org_id == Organization.id).where(Membership.user_id == principal.user.id)
    ).all()
    return {
        "user": user_dict(principal.user),
        "active_org": org_dict(principal.org, principal.role),
        "orgs": [org_dict(o, r) for o, r in rows],
        "usage": usage_summary(db, get_settings(), principal.org),
    }


# ----------------------------------------------------------------------------- organizations
class OrgBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberBody(BaseModel):
    email: str
    role: str = "member"


@router.post("/orgs", status_code=201)
def create_org(body: OrgBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    org = Organization(name=body.name.strip(), plan="free", personal=False)
    db.add(org)
    db.flush()
    db.add(Membership(user_id=principal.user.id, org_id=org.id, role="owner"))
    audit(db, principal, "org.create", {"org_id": org.id}, org_id=org.id)
    return org_dict(org, "owner")


@router.get("/orgs/current/members")
def list_members(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(
        select(User, Membership.role).join(Membership, Membership.user_id == User.id).where(Membership.org_id == principal.org.id)
    ).all()
    return [{**user_dict(u), "role": r} for u, r in rows]


@router.post("/orgs/current/members", status_code=201)
def add_member(body: MemberBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    if body.role not in ROLES or (body.role == "owner" and principal.role != "owner"):
        raise HTTPException(422, "Invalid role.") from None
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        raise HTTPException(404, "No user with that email. Ask them to register first.") from None
    if db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.org_id == principal.org.id)):
        raise HTTPException(409, "That user is already a member.") from None
    try:
        check_can_add_seat(db, get_settings(), principal.org)
    except PlanLimitError as exc:
        raise _limit_error(exc) from None
    db.add(Membership(user_id=user.id, org_id=principal.org.id, role=body.role))
    audit(db, principal, "member.add", {"user_id": user.id, "role": body.role})
    return {"user_id": user.id, "role": body.role}


@router.patch("/orgs/current/members/{user_id}")
def set_role(user_id: str, body: MemberBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    member = db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.org_id == principal.org.id))
    if member is None:
        raise HTTPException(404, "Member not found.") from None
    if body.role not in ROLES:
        raise HTTPException(422, "Invalid role.") from None
    if (member.role == "owner" or body.role == "owner") and principal.role != "owner":
        raise HTTPException(403, "Only an owner can change owner roles.") from None
    if member.role == "owner" and body.role != "owner" and _owner_count(db, principal.org.id) <= 1:
        raise HTTPException(409, "An organization needs at least one owner.") from None
    member.role = body.role
    audit(db, principal, "member.role", {"user_id": user_id, "role": body.role})
    return {"user_id": user_id, "role": body.role}


def _owner_count(db: Session, org_id: str) -> int:
    return db.scalar(select(func.count(Membership.id)).where(Membership.org_id == org_id, Membership.role == "owner")) or 0


@router.delete("/orgs/current/members/{user_id}", status_code=204)
def remove_member(user_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if user_id != principal.user.id:
        principal.require("admin")
    member = db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.org_id == principal.org.id))
    if member is None:
        raise HTTPException(404, "Member not found.") from None
    if member.role == "owner" and (principal.role != "owner" or _owner_count(db, principal.org.id) <= 1):
        raise HTTPException(409, "An owner cannot be removed unless another owner remains.") from None
    db.delete(member)
    for key in db.scalars(select(ApiKey).where(ApiKey.org_id == principal.org.id, ApiKey.user_id == user_id)):
        key.revoked_at = key.revoked_at or utcnow()
    audit(db, principal, "member.remove", {"user_id": user_id})


# ----------------------------------------------------------------------------- API keys
class KeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.post("/keys", status_code=201)
def create_key(body: KeyBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if principal.via == "api_key":
        raise HTTPException(403, "API keys cannot create other API keys.") from None
    principal.require("member")
    plaintext, prefix, digest = generate_api_key()
    key = ApiKey(org_id=principal.org.id, user_id=principal.user.id, name=body.name.strip(), prefix=prefix, key_hash=digest)
    db.add(key)
    db.flush()
    audit(db, principal, "key.create", {"key_id": key.id})
    return {**key_dict(key), "key": plaintext}  # the plaintext is shown only once


@router.get("/keys")
def list_keys(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    query = select(ApiKey).where(ApiKey.org_id == principal.org.id)
    if ROLES.index(principal.role) < ROLES.index("admin"):
        query = query.where(ApiKey.user_id == principal.user.id)
    return [key_dict(k) for k in db.scalars(query.order_by(ApiKey.created_at.desc()))]


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None or key.org_id != principal.org.id:
        raise HTTPException(404, "Key not found.") from None
    if key.user_id != principal.user.id:
        principal.require("admin")
    key.revoked_at = key.revoked_at or utcnow()
    audit(db, principal, "key.revoke", {"key_id": key_id})


# ----------------------------------------------------------------------------- jobs
class UrlJob(BaseModel):
    url: str = Field(max_length=2000)
    options: JobOptions = JobOptions()


def _new_job(db: Session, principal: Principal, **fields) -> Job:
    job = Job(org_id=principal.org.id, user_id=principal.user.id, **fields)
    db.add(job)
    db.flush()
    audit(db, principal, "job.create", {"job_id": job.id, "kind": job.kind})
    return job


@router.post("/jobs", status_code=201)
def create_job(body: UrlJob, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("member")
    settings = get_settings()
    try:
        url = validate_source_url(body.url, allow_private=settings.allow_private_urls)
        check_can_submit(db, settings, principal.org)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from None
    except PlanLimitError as exc:
        raise _limit_error(exc) from None
    job = _new_job(db, principal, source_type="url", source_ref=url, options=body.options.model_dump())
    return job_dict(job, settings)


@router.post("/jobs/upload", status_code=201)
def create_upload_job(
    request: Request,
    file: UploadFile = File(...),
    options: str = Form("{}"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    principal.require("member")
    settings = get_settings()
    try:
        opts = JobOptions(**json.loads(options or "{}"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"Invalid options: {exc}") from None
    declared = int(request.headers.get("content-length") or 0)
    try:
        plan = check_can_submit(db, settings, principal.org, upload_bytes=declared)
    except PlanLimitError as exc:
        raise _limit_error(exc) from None
    job_id = new_id()
    limit = min(plan.max_upload_bytes or settings.max_upload_bytes, settings.max_upload_bytes)
    try:
        path = store_upload(file.file, file.filename or "upload", settings.uploads_dir / job_id, limit)
    except IngestError as exc:
        shutil.rmtree(settings.uploads_dir / job_id, ignore_errors=True)
        raise HTTPException(413, str(exc)) from None
    job = Job(id=job_id, org_id=principal.org.id, user_id=principal.user.id, source_type="upload", source_ref=file.filename or "upload",
              source_path=str(path), options=opts.model_dump())
    db.add(job)
    db.flush()
    audit(db, principal, "job.create", {"job_id": job.id, "kind": "upload"})
    return job_dict(job, settings)


@router.get("/jobs")
def list_jobs(
    status: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    query = select(Job).where(Job.org_id == principal.org.id, Job.kind == "pipeline")
    if status:
        query = query.where(Job.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(query.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    counts = dict(db.execute(select(Clip.job_id, func.count(Clip.id)).where(Clip.job_id.in_([j.id for j in jobs]), Clip.status == "ready").group_by(Clip.job_id)).all()) if jobs else {}
    return {"total": total, "items": [{**job_dict(j, settings), "clip_count": counts.get(j.id, 0)} for j in jobs]}


def _own_job(db: Session, principal: Principal, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.org_id != principal.org.id:
        raise HTTPException(404, "Job not found.") from None
    return job


@router.get("/jobs/{job_id}")
def get_job(job_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    job = _own_job(db, principal, job_id)
    clips = db.scalars(select(Clip).where(Clip.job_id == job.id)).all()
    return job_dict(job, get_settings(), clips)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("member")
    job = _own_job(db, principal, job_id)
    if job.status == "queued":
        job.status, job.finished_at, job.stage = "canceled", utcnow(), "canceled"
    elif job.status == "running":
        job.cancel_requested = True
    else:
        raise HTTPException(409, f"The job already {job.status}.") from None
    audit(db, principal, "job.cancel", {"job_id": job.id})
    return job_dict(job, get_settings())


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("member")
    job = _own_job(db, principal, job_id)
    if job.status in ("queued", "running"):
        raise HTTPException(409, "Cancel the job before deleting it.") from None
    settings = get_settings()
    for child in db.scalars(select(Job).where(Job.kind == "rerender", Job.options["clip_id"].as_string().in_(select(Clip.id).where(Clip.job_id == job.id)))):
        db.delete(child)
    shutil.rmtree(settings.clips_dir / job.id, ignore_errors=True)
    shutil.rmtree(settings.uploads_dir / job.id, ignore_errors=True)
    db.delete(job)
    audit(db, principal, "job.delete", {"job_id": job_id})


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, principal: Principal = Depends(get_principal)):
    """Server-sent events with the job's progress until it finishes."""
    settings = get_settings()
    org_id = principal.org.id

    def snapshot() -> dict | None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None or job.org_id != org_id:
                return None
            clips = db.scalars(select(Clip).where(Clip.job_id == job.id)).all() if job.status in ("succeeded", "failed", "canceled") else None
            return job_dict(job, settings, clips)

    if await run_in_threadpool(snapshot) is None:
        raise HTTPException(404, "Job not found.") from None

    async def stream():
        last = None
        for _ in range(60 * 60 * 6):  # at most six hours
            data = await run_in_threadpool(snapshot)
            if data is None:
                break
            fingerprint = (data["status"], data["stage"], data["progress"])
            if fingerprint != last:
                last = fingerprint
                yield f"event: progress\ndata: {json.dumps(data)}\n\n"
            else:
                yield ": keep-alive\n\n"
            if data["status"] in ("succeeded", "failed", "canceled"):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------------------- clips
def _own_clip(db: Session, principal: Principal, clip_id: str) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None or clip.org_id != principal.org.id:
        raise HTTPException(404, "Clip not found.") from None
    return clip


class ClipPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    hook: str | None = Field(None, max_length=300)


@router.get("/clips/{clip_id}")
def get_clip(clip_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return clip_dict(_own_clip(db, principal, clip_id), get_settings())


@router.patch("/clips/{clip_id}")
def patch_clip(clip_id: str, body: ClipPatch, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("member")
    clip = _own_clip(db, principal, clip_id)
    if body.title is not None:
        clip.title = body.title.strip()
    if body.hook is not None:
        clip.hook = body.hook.strip()
    audit(db, principal, "clip.edit", {"clip_id": clip.id})
    return clip_dict(clip, get_settings())


class RerenderBody(BaseModel):
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    caption_style: str | None = None
    layout: str | None = None
    hook_title: bool | None = None


@router.post("/clips/{clip_id}/rerender", status_code=202)
def rerender_clip(clip_id: str, body: RerenderBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Trim, restyle or re-layout a clip. Runs as a background job; the clip is ``rendering`` meanwhile."""
    principal.require("member")
    clip = _own_clip(db, principal, clip_id)
    try:
        opts = RerenderOptions(clip_id=clip.id, **body.model_dump(exclude_none=True))
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    start, end = opts.start if opts.start is not None else clip.start, opts.end if opts.end is not None else clip.end
    if end - start < 1.0 or end - start > 300:
        raise HTTPException(422, "A clip must be between 1 and 300 seconds long.") from None
    if db.scalar(select(func.count(Job.id)).where(Job.kind == "rerender", Job.status.in_(("queued", "running")), Job.options["clip_id"].as_string() == clip.id)):
        raise HTTPException(409, "This clip is already being re-rendered.") from None
    settings = get_settings()
    job = _new_job(db, principal, kind="rerender", source_type="clip", source_ref=clip.id, options=opts.model_dump(exclude_none=True))
    clip.status = "rendering"
    return job_dict(job, settings)


@router.delete("/clips/{clip_id}", status_code=204)
def delete_clip(clip_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("member")
    clip = _own_clip(db, principal, clip_id)
    for p in (clip.path, clip.thumb_path):
        if p:
            Path(p).unlink(missing_ok=True)
    db.delete(clip)
    audit(db, principal, "clip.delete", {"clip_id": clip_id})


@media_router.get("/media/{clip_id}/{kind}")
def media(clip_id: str, kind: str, exp: int, sig: str, db: Session = Depends(get_db)):
    """Signed, expiring media links (so <video> tags work without exposing tokens)."""
    settings = get_settings()
    if kind not in ("video", "thumb") or not verify_media(settings.secret_key, clip_id, kind, exp, sig):
        raise HTTPException(403, "Invalid or expired link.") from None
    clip = db.get(Clip, clip_id)
    path = Path(clip.path if kind == "video" else clip.thumb_path) if clip else None
    if clip is None or not path or not path.is_file():
        raise HTTPException(404, "File not found.") from None
    root = settings.clips_dir.resolve()
    if root not in path.resolve().parents:
        raise HTTPException(404, "File not found.") from None
    filename = f"{(clip.title or 'clip')[:60]}".strip().replace("/", "-") + (".mp4" if kind == "video" else ".jpg")
    return FileResponse(path, media_type="video/mp4" if kind == "video" else "image/jpeg", filename=filename if kind == "video" else None)


# ----------------------------------------------------------------------------- webhooks
class WebhookBody(BaseModel):
    url: str = Field(max_length=2000)
    events: list[str] = Field(default_factory=lambda: ["*"])


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookBody, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    settings = get_settings()
    try:
        url = validate_source_url(body.url, allow_private=settings.allow_private_urls)
    except UnsafeURLError as exc:
        raise HTTPException(400, str(exc)) from None
    bad = [e for e in body.events if e != "*" and e not in EVENTS]
    if bad:
        raise HTTPException(422, f"Unknown events: {', '.join(bad)}. Valid: {', '.join(EVENTS)}") from None
    secret = "whsec_" + secrets.token_urlsafe(24)
    hook = Webhook(org_id=principal.org.id, url=url, secret=secret, events=body.events or ["*"])
    db.add(hook)
    db.flush()
    audit(db, principal, "webhook.create", {"webhook_id": hook.id})
    return {"id": hook.id, "url": hook.url, "events": hook.events, "secret": secret}


@router.get("/webhooks")
def list_webhooks(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    return [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in db.scalars(select(Webhook).where(Webhook.org_id == principal.org.id))]


@router.get("/webhooks/{webhook_id}/deliveries")
def deliveries(webhook_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    hook = db.get(Webhook, webhook_id)
    if hook is None or hook.org_id != principal.org.id:
        raise HTTPException(404, "Webhook not found.") from None
    rows = db.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook.id).order_by(WebhookDelivery.created_at.desc()).limit(50))
    return [{"event": d.event, "ok": d.ok, "status_code": d.status_code, "attempt": d.attempt, "error": d.error, "at": iso(d.created_at)} for d in rows]


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    hook = db.get(Webhook, webhook_id)
    if hook is None or hook.org_id != principal.org.id:
        raise HTTPException(404, "Webhook not found.") from None
    db.delete(hook)
    audit(db, principal, "webhook.delete", {"webhook_id": webhook_id})


# ----------------------------------------------------------------------------- usage, audit
@router.get("/usage")
def usage(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return usage_summary(db, get_settings(), principal.org)


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=200), principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require("admin")
    rows = db.scalars(select(AuditLog).where(AuditLog.org_id == principal.org.id).order_by(AuditLog.created_at.desc()).limit(limit))
    return [{"action": r.action, "user_id": r.user_id, "detail": r.detail, "at": iso(r.created_at)} for r in rows]


# ----------------------------------------------------------------------------- instance admin
@router.get("/admin/stats")
def admin_stats(_: Principal = Depends(require_instance_admin), db: Session = Depends(get_db)):
    by_status = dict(db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    return {
        "users": db.scalar(select(func.count(User.id))), "orgs": db.scalar(select(func.count(Organization.id))),
        "clips": db.scalar(select(func.count(Clip.id))), "jobs": by_status,
        "source_minutes_total": round(db.scalar(select(func.coalesce(func.sum(UsageEvent.quantity), 0.0))) or 0.0, 1),
    }


@router.get("/admin/users")
def admin_users(_: Principal = Depends(require_instance_admin), db: Session = Depends(get_db)):
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.created_at))]


class UserPatch(BaseModel):
    disabled: bool | None = None
    is_admin: bool | None = None


@router.patch("/admin/users/{user_id}")
def admin_patch_user(user_id: str, body: UserPatch, principal: Principal = Depends(require_instance_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.") from None
    if user.id == principal.user.id and (body.disabled or body.is_admin is False):
        raise HTTPException(409, "You cannot disable or demote yourself.") from None
    if body.disabled is not None:
        user.disabled = body.disabled
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    audit(db, principal, "admin.user", {"user_id": user_id, **body.model_dump(exclude_none=True)})
    return user_dict(user)


class PlanPatch(BaseModel):
    plan: str


@router.patch("/admin/orgs/{org_id}")
def admin_set_plan(org_id: str, body: PlanPatch, principal: Principal = Depends(require_instance_admin), db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(404, "Organization not found.") from None
    if body.plan not in PLANS:
        raise HTTPException(422, f"Unknown plan. Valid: {', '.join(PLANS)}") from None
    org.plan = body.plan
    audit(db, principal, "admin.plan", {"org_id": org_id, "plan": body.plan}, org_id=org_id)
    return org_dict(org)


# ----------------------------------------------------------------------------- health / metrics
@router.get("/health")
def health():
    return {"status": "ok"}


def readiness(db: Session = Depends(get_db)):
    settings = get_settings()
    db.execute(select(1))
    missing = [b for b in (settings.ffmpeg_path, settings.ffprobe_path) if not shutil.which(b)]
    if missing:
        raise HTTPException(503, f"Missing executables: {', '.join(missing)}") from None
    return {"status": "ready"}


router.add_api_route("/ready", readiness, methods=["GET"])


def metrics(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.metrics_token:
        raise HTTPException(404, "Not found.") from None
    if request.headers.get("authorization", "") != f"Bearer {settings.metrics_token}":
        raise HTTPException(401, "Unauthorized.") from None
    lines = ["# TYPE reelwright_jobs gauge"]
    for status, count in db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all():
        lines.append(f'reelwright_jobs{{status="{status}"}} {count}')
    lines += ["# TYPE reelwright_clips gauge", f"reelwright_clips {db.scalar(select(func.count(Clip.id))) or 0}"]
    return PlainTextResponse("\n".join(lines) + "\n")


router.add_api_route("/metrics", metrics, methods=["GET"])
