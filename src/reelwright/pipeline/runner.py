"""Runs one job end to end and records progress, clips and usage."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import Settings, get_settings
from ..db import session_scope, utcnow
from ..models import Clip, Job, Organization
from ..options import JobOptions, RerenderOptions
from ..plans import effective_plan, record_usage
from ..webhooks import dispatch
from .ingest import IngestError, fetch_source
from .llm import get_llm
from .media import MediaError, extract_audio, probe
from .reframe import plan_reframe
from .render import PLATFORM_MAX_SECONDS, render_clip
from .segments import select_clips
from .transcribe import Transcript, TranscriptionError, get_transcriber

log = logging.getLogger("reelwright.runner")


class JobCanceled(Exception):
    pass


class JobFailed(Exception):
    pass


class Reporter:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, stage: str, progress: float) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job is None or job.status == "canceled" or job.cancel_requested:
                raise JobCanceled()
            job.stage, job.progress, job.heartbeat_at = stage, round(min(progress, 0.99), 3), utcnow()


def process_job(job_id: str, settings: Settings | None = None) -> str:
    """Run a claimed job; returns its final status."""
    settings = settings or get_settings()
    reporter = Reporter(job_id)
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return "missing"
        kind, org_id = job.kind, job.org_id
    status, error = "succeeded", ""
    try:
        if kind == "rerender":
            _run_rerender(job_id, settings, reporter)
        else:
            _run_pipeline(job_id, settings, reporter)
    except JobCanceled:
        status = "canceled"
    except (JobFailed, IngestError, TranscriptionError, MediaError) as exc:
        status, error = "failed", str(exc)
    except Exception as exc:  # unexpected: keep the worker alive, surface the message
        log.exception("Job %s crashed", job_id)
        status, error = "failed", f"Unexpected error: {exc}"
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status, job.error, job.finished_at = status, error[:2000], utcnow()
        if status == "succeeded":
            job.progress, job.stage = 1.0, "done"
        payload = {"job_id": job.id, "status": status, "error": error, "title": job.title, "kind": job.kind}
    shutil.rmtree(settings.work_dir / job_id, ignore_errors=True)
    dispatch(org_id, f"job.{status}", payload, settings=settings)
    return status


def _run_pipeline(job_id: str, settings: Settings, reporter: Reporter) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        options = JobOptions(**(job.options or {}))
        source_type, source_ref, source_path, org_id = job.source_type, job.source_ref, job.source_path, job.org_id
        org = db.get(Organization, org_id)
        plan = effective_plan(settings, org)
    work = settings.work_dir / job_id
    work.mkdir(parents=True, exist_ok=True)

    reporter.update("ingest", 0.02)
    if source_type == "url":
        path, title = fetch_source(source_ref, settings.uploads_dir / job_id, settings, lambda f: reporter.update("ingest", 0.02 + 0.06 * f))
    else:
        path, title = Path(source_path), Path(source_ref).stem or "Upload"
    if not path.exists():
        raise JobFailed("The source file is missing.")
    info = probe(path, settings)
    if not info.has_video or info.duration <= 0:
        raise JobFailed("The file has no video stream.")
    if info.duration > settings.max_source_minutes * 60:
        raise JobFailed(f"The video is longer than the {settings.max_source_minutes}-minute limit.")
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.source_path, job.title, job.source_seconds = str(path), title[:500], info.duration

    reporter.update("transcribing", 0.10)
    audio = extract_audio(path, work / "audio.wav", settings)
    transcript = get_transcriber(settings, path).transcribe(audio, options.language)
    clip_dir = settings.clips_dir / job_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    transcript.save(clip_dir / "transcript.json")

    reporter.update("selecting", 0.50)
    max_seconds = min(options.max_seconds, PLATFORM_MAX_SECONDS[options.platform])
    min_seconds = min(options.min_seconds, max_seconds - 1)
    limit = min(options.max_clips, plan.max_clips_per_job)
    plans = select_clips(
        transcript, info.duration, max_clips=limit, min_seconds=min_seconds, max_seconds=max_seconds,
        llm=get_llm(settings) if options.use_llm else None,
    )
    if not plans:
        raise JobFailed("No clips could be found in this video.")

    rendered = 0
    for i, cp in enumerate(plans):
        reporter.update("rendering", 0.55 + 0.42 * i / len(plans))
        words = transcript.slice(cp.start, cp.end)
        with session_scope() as db:
            clip = Clip(
                job_id=job_id, org_id=org_id, idx=i, start=cp.start, end=cp.end, title=cp.title, hook=cp.hook,
                reason=cp.reason, scores=cp.scores, caption_style=options.caption_style, layout=options.layout, status="rendering",
            )
            db.add(clip)
            db.flush()
            clip_id = clip.id
        try:
            rp = plan_reframe(path, info.width, info.height, cp.start, cp.end, layout=options.layout)
            result = render_clip(
                settings, path, clip_dir / f"{clip_id}.mp4", info, rp, start=cp.start, end=cp.end, words=words,
                caption_style=options.caption_style, title=cp.hook if options.hook_title else None,
            )
        except MediaError as exc:
            log.warning("Clip %s failed: %s", clip_id, exc)
            with session_scope() as db:
                db.get(Clip, clip_id).status = "failed"
            continue
        rendered += 1
        with session_scope() as db:
            c = db.get(Clip, clip_id)
            c.status, c.path, c.thumb_path = "ready", str(result.path), str(result.thumb)
            c.width, c.height, c.size_bytes, c.layout = result.width, result.height, result.size_bytes, rp.layout
    if rendered == 0:
        raise JobFailed("Every clip failed to render.")
    with session_scope() as db:
        record_usage(db, org_id, job_id, info.duration)


def _run_rerender(job_id: str, settings: Settings, reporter: Reporter) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        opts = RerenderOptions(**(job.options or {}))
        clip = db.get(Clip, opts.clip_id)
        if clip is None or clip.org_id != job.org_id:
            raise JobFailed("The clip no longer exists.")
        parent = db.get(Job, clip.job_id)
        src = Path(parent.source_path)
        start = opts.start if opts.start is not None else clip.start
        end = opts.end if opts.end is not None else clip.end
        style = opts.caption_style or clip.caption_style
        layout = opts.layout or clip.layout
        hook_on = True if opts.hook_title is None else opts.hook_title
        hook, parent_id, clip_id = clip.hook, clip.job_id, clip.id
        clip.status = "rendering"
    if not src.exists():
        raise JobFailed("The source video was removed; the clip cannot be re-rendered.")
    info = probe(src, settings)
    end = min(end, info.duration)
    if end - start < 1.0:
        raise JobFailed("The clip must be at least one second long.")
    reporter.update("rendering", 0.2)
    transcript_file = settings.clips_dir / parent_id / "transcript.json"
    words = Transcript.load(transcript_file).slice(start, end) if transcript_file.exists() else []
    try:
        rp = plan_reframe(src, info.width, info.height, start, end, layout=layout)
        out = settings.clips_dir / parent_id / f"{clip_id}.mp4"
        tmp_out = out.with_suffix(".new.mp4")
        result = render_clip(settings, src, tmp_out, info, rp, start=start, end=end, words=words, caption_style=style, title=hook if hook_on else None)
        tmp_out.replace(out)
        result.thumb.replace(out.with_suffix(".jpg"))
    except MediaError:
        with session_scope() as db:
            db.get(Clip, clip_id).status = "failed"
        raise
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        c.start, c.end, c.caption_style, c.layout, c.status = start, end, style, rp.layout, "ready"
        c.path, c.thumb_path, c.size_bytes = str(out), str(out.with_suffix(".jpg")), out.stat().st_size
