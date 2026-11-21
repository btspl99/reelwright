"""Fetching the source video."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import Settings
from .urls import validate_source_url

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav")


class IngestError(RuntimeError):
    pass


def safe_filename(name: str, default: str = "source") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return base[:120] or default


def _direct_download(url: str, dest_dir: Path, settings: Settings) -> tuple[Path, str]:
    name = safe_filename(Path(url.split("?", 1)[0]).name)
    target = dest_dir / name
    limit = settings.max_upload_bytes
    written = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        if response.status_code >= 400:
            raise IngestError(f"Download failed with HTTP {response.status_code}.")
        # Re-validate the final URL after redirects.
        validate_source_url(str(response.url), allow_private=settings.allow_private_urls)
        with target.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise IngestError("The remote file is larger than the allowed size.")
                handle.write(chunk)
    return target, target.stem


def fetch_source(
    url: str,
    dest_dir: Path,
    settings: Settings,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, str]:
    """Download ``url`` into ``dest_dir``; return ``(path, title)``.

    Uses ``yt-dlp`` (``pip install reelwright[download]``) for video sites. Plain links to video files are
    downloaded directly when yt-dlp is not installed. Only download content you have the right to use.
    """
    validate_source_url(url, allow_private=settings.allow_private_urls)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        yt_dlp = None

    if yt_dlp is None:
        if url.lower().split("?", 1)[0].endswith(VIDEO_EXTENSIONS):
            return _direct_download(url, dest_dir, settings)
        raise IngestError("yt-dlp is not installed; install reelwright[download] to import from video sites.")

    def hook(status: dict) -> None:
        if on_progress and status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(1.0, status.get("downloaded_bytes", 0) / total))

    options = {
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 30,
        "retries": 3,
        "max_filesize": settings.max_upload_bytes,
        "match_filter": yt_dlp.utils.match_filter_func(f"!is_live & duration <= {settings.max_source_minutes * 60}"),
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp raises many types
        raise IngestError(f"Could not download the video: {exc}") from exc
    if not info:
        raise IngestError("The video was filtered out (live stream, too long, or too large).")
    path = Path(ydl.prepare_filename(info))
    if not path.exists():
        candidates = sorted(dest_dir.glob(f"{info.get('id', '*')}.*"))
        if not candidates:
            raise IngestError("The download finished but no file was produced.")
        path = candidates[0]
    return path, str(info.get("title") or path.stem)


def store_upload(stream, filename: str, dest_dir: Path, max_bytes: int) -> Path:
    """Copy an uploaded file to disk, enforcing a size limit."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / safe_filename(filename)
    written = 0
    with target.open("wb") as handle:
        while chunk := stream.read(1 << 20):
            written += len(chunk)
            if written > max_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise IngestError("The uploaded file is larger than the allowed size.")
            handle.write(chunk)
    return target


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
