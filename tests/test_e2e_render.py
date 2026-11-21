"""Full pipeline on a synthetic video: sidecar transcript, no LLM, real ffmpeg."""

import json
import shutil
import subprocess

import pytest
from sqlalchemy import select

from reelwright.db import session_scope
from reelwright.models import Clip, Job, Membership, Organization, User
from reelwright.pipeline.runner import process_job

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def make_video(path, seconds=45):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=25:d={seconds}",
         "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)], check=True)


def sidecar(path, seconds=45):
    words, t = [], 0.5
    text = "Here is the surprising truth about saving money that nobody tells you. Automate it because small habits compound."
    while t < seconds - 2:
        for w in text.split():
            words.append({"text": w, "start": round(t, 2), "end": round(t + 0.35, 2)}); t += 0.4
        t += 1.0
    (path.parent / (path.name + ".transcript.json")).write_text(json.dumps({"language": "en", "words": words}))


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"][0]


def test_upload_job_renders_vertical_clips(settings):
    with session_scope() as db:
        u = User(email="e@example.com", password_hash="x"); o = Organization(name="o", plan="free", personal=True)
        db.add_all([u, o]); db.flush(); db.add(Membership(user_id=u.id, org_id=o.id, role="owner"))
        job = Job(org_id=o.id, user_id=u.id, source_type="upload", source_ref="demo.mp4",
                  options={"max_clips": 2, "min_seconds": 8, "max_seconds": 20, "caption_style": "pop", "use_llm": False})
        db.add(job); db.flush(); job_id = job.id
    folder = settings.uploads_dir / job_id
    folder.mkdir(parents=True)
    media = folder / "demo.mp4"
    make_video(media); sidecar(media)
    with session_scope() as db:
        db.get(Job, job_id).source_path = str(media)

    assert process_job(job_id, settings) == "succeeded"
    with session_scope() as db:
        clips = db.scalars(select(Clip).where(Clip.job_id == job_id, Clip.status == "ready")).all()
        assert 1 <= len(clips) <= 2
        for c in clips:
            info = probe(c.path)
            assert (info["width"], info["height"], info["codec_name"]) == (1080, 1920, "h264")
            assert c.thumb_path and shutil.os.path.getsize(c.thumb_path) > 0


def test_missing_source_fails_cleanly(settings):
    with session_scope() as db:
        u = User(email="f@example.com", password_hash="x"); o = Organization(name="o", plan="free", personal=True)
        db.add_all([u, o]); db.flush()
        job = Job(org_id=o.id, user_id=u.id, source_type="upload", source_ref="gone.mp4", source_path="/nonexistent.mp4", options={})
        db.add(job); db.flush(); job_id = job.id
    assert process_job(job_id, settings) == "failed"
    with session_scope() as db:
        assert db.get(Job, job_id).error
