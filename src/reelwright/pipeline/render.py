"""Rendering a clip with ffmpeg: reframe, burn captions, encode to H.264/AAC."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from .captions import build_ass
from .media import MediaInfo, extract_thumbnail, probe, run
from .reframe import TARGET_H, TARGET_W, ReframePlan, filter_graph
from .transcribe import Word

ENCODERS: dict[str, list[str]] = {
    "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-b:v", "0"],
    "h264_videotoolbox": ["-c:v", "h264_videotoolbox", "-b:v", "8M"],
}

PLATFORM_MAX_SECONDS = {"shorts": 60, "tiktok": 180, "reels": 90}


@dataclass(frozen=True)
class RenderResult:
    path: Path
    thumb: Path
    width: int
    height: int
    size_bytes: int
    duration: float


def build_command(
    settings: Settings, src: Path, out_name: str, start: float, duration: float, *, has_audio: bool
) -> list[str]:
    encoder = ENCODERS.get(settings.video_encoder, ENCODERS["libx264"])
    cmd = [
        settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
        "-filter_script:v", "graph.txt", "-map", "0:v:0",
    ]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += encoder + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", out_name]
    return cmd


def render_clip(
    settings: Settings,
    src: Path,
    out_path: Path,
    info: MediaInfo,
    plan: ReframePlan,
    *,
    start: float,
    end: float,
    words: list[Word],
    caption_style: str = "pop",
    title: str | None = None,
) -> RenderResult:
    """Render ``[start, end]`` of ``src`` to ``out_path`` as a 1080x1920 clip."""
    duration = max(end - start, 0.5)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    graph = filter_graph(plan, info.width, info.height)
    has_captions = caption_style != "none" or bool(title)
    with tempfile.TemporaryDirectory(prefix="rw-render-", dir=settings.work_dir if settings.work_dir.exists() else None) as tmp:
        tmpdir = Path(tmp)
        if has_captions:
            ass = build_ass(words, start, end, style=caption_style, font=settings.caption_font, title=title)
            (tmpdir / "captions.ass").write_text(ass, encoding="utf-8")
            graph += ",subtitles=filename=captions.ass"
        (tmpdir / "graph.txt").write_text(graph)
        run(build_command(settings, src.resolve(), "out.mp4", start, duration, has_audio=info.has_audio), cwd=tmpdir, timeout=3600)
        shutil.move(str(tmpdir / "out.mp4"), str(out_path))
    thumb = out_path.with_suffix(".jpg")
    extract_thumbnail(out_path, thumb, min(1.0, duration / 2), settings)
    result_info = probe(out_path, settings)
    return RenderResult(out_path, thumb, result_info.width or TARGET_W, result_info.height or TARGET_H, out_path.stat().st_size, result_info.duration)
