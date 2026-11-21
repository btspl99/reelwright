"""Turning database rows into JSON-friendly dicts."""

from __future__ import annotations

import time

from ..config import Settings
from ..models import ApiKey, Clip, Job, Organization, User
from ..security import sign_media

MEDIA_TTL = 3600


def media_url(settings: Settings, clip_id: str, kind: str) -> str:
    exp = int(time.time()) + MEDIA_TTL
    return f"/media/{clip_id}/{kind}?exp={exp}&sig={sign_media(settings.secret_key, clip_id, kind, exp)}"


def iso(value) -> str | None:
    return value.isoformat() + "Z" if value else None


def clip_dict(clip: Clip, settings: Settings) -> dict:
    ready = clip.status == "ready"
    return {
        "id": clip.id, "job_id": clip.job_id, "index": clip.idx, "start": clip.start, "end": clip.end,
        "duration": round(clip.end - clip.start, 2), "title": clip.title, "hook": clip.hook, "reason": clip.reason,
        "scores": clip.scores, "caption_style": clip.caption_style, "layout": clip.layout, "status": clip.status,
        "width": clip.width, "height": clip.height, "size_bytes": clip.size_bytes,
        "video_url": media_url(settings, clip.id, "video") if ready else None,
        "thumb_url": media_url(settings, clip.id, "thumb") if ready and clip.thumb_path else None,
        "created_at": iso(clip.created_at),
    }


def job_dict(job: Job, settings: Settings, clips: list[Clip] | None = None) -> dict:
    data = {
        "id": job.id, "kind": job.kind, "status": job.status, "stage": job.stage, "progress": job.progress,
        "source_type": job.source_type, "source": job.source_ref, "title": job.title, "options": job.options,
        "error": job.error, "source_seconds": job.source_seconds, "created_at": iso(job.created_at),
        "started_at": iso(job.started_at), "finished_at": iso(job.finished_at),
    }
    if clips is not None:
        data["clips"] = [clip_dict(c, settings) for c in sorted(clips, key=lambda c: c.idx)]
    return data


def user_dict(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin, "disabled": user.disabled}


def org_dict(org: Organization, role: str | None = None) -> dict:
    data = {"id": org.id, "name": org.name, "plan": org.plan, "personal": org.personal}
    if role:
        data["role"] = role
    return data


def key_dict(key: ApiKey) -> dict:
    return {
        "id": key.id, "name": key.name, "prefix": key.prefix, "created_at": iso(key.created_at),
        "last_used_at": iso(key.last_used_at), "revoked": key.revoked_at is not None,
    }
