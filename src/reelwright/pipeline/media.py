"""Thin, safe wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    has_video: bool


def run(cmd: list[str], *, cwd: Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a command without a shell; raise :class:`MediaError` with the tail of stderr on failure."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise MediaError(f"Executable not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).name} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise MediaError(f"{Path(cmd[0]).name} failed ({proc.returncode}): " + " | ".join(tail))
    return proc


def _fraction(value: str) -> float:
    try:
        num, _, den = value.partition("/")
        return float(num) / float(den) if den and float(den) else float(num)
    except ValueError:
        return 0.0


def probe(path: Path, settings: Settings) -> MediaInfo:
    proc = run(
        [settings.ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=120,
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or (video or {}).get("duration") or 0.0)
    return MediaInfo(
        duration=duration,
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        fps=_fraction((video or {}).get("avg_frame_rate", "0/1")) if video else 0.0,
        has_audio=audio is not None,
        has_video=video is not None,
    )


def extract_audio(src: Path, dst: Path, settings: Settings) -> Path:
    """16 kHz mono WAV, the input format speech models expect."""
    run(
        [settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        timeout=3600,
    )
    return dst


def extract_thumbnail(src: Path, dst: Path, at: float, settings: Settings, width: int = 360) -> Path:
    run(
        [settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dst)],
        timeout=120,
    )
    return dst
