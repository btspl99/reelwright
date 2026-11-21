"""Runtime settings, read from ``REELWRIGHT_*`` environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"REELWRIGHT_{name}", default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = _env(name)
    try:
        return max(minimum, int(raw)) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = _env(name)
    try:
        return max(minimum, float(raw)) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    secret_key: str
    public_url: str
    registration_open: bool
    enforce_plans: bool
    embedded_worker: bool
    worker_poll_seconds: float
    worker_concurrency: int
    stale_job_seconds: int
    max_upload_bytes: int
    max_source_minutes: int
    allow_private_urls: bool
    transcription_provider: str
    whisper_model: str
    whisper_device: str
    transcription_base_url: str
    transcription_api_key: str
    transcription_model: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    video_encoder: str
    ffmpeg_path: str
    ffprobe_path: str
    caption_font: str
    token_ttl_hours: int
    cors_origins: tuple[str, ...]
    metrics_token: str

    # --- derived locations -------------------------------------------------
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.work_dir, self.clips_dir):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(_env("DATA_DIR", "./data")).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        secret = _env("SECRET_KEY")
        if not secret:
            secret_file = data_dir / "secret_key"
            if secret_file.exists():
                secret = secret_file.read_text().strip()
            else:
                secret = secrets.token_urlsafe(48)
                secret_file.write_text(secret)
                secret_file.chmod(0o600)
        origins = tuple(o.strip() for o in _env("CORS_ORIGINS").split(",") if o.strip())
        return cls(
            data_dir=data_dir,
            database_url=_env("DATABASE_URL", f"sqlite:///{data_dir / 'reelwright.db'}"),
            secret_key=secret,
            public_url=_env("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
            registration_open=_bool("REGISTRATION_OPEN", True),
            enforce_plans=_bool("ENFORCE_PLANS", False),
            embedded_worker=_bool("EMBEDDED_WORKER", True),
            worker_poll_seconds=_float("WORKER_POLL_SECONDS", 2.0, 0.05),
            worker_concurrency=_int("WORKER_CONCURRENCY", 1, 1),
            stale_job_seconds=_int("STALE_JOB_SECONDS", 900, 30),
            max_upload_bytes=_int("MAX_UPLOAD_BYTES", 4_000_000_000, 1),
            max_source_minutes=_int("MAX_SOURCE_MINUTES", 240, 1),
            allow_private_urls=_bool("ALLOW_PRIVATE_URLS", False),
            transcription_provider=_env("TRANSCRIPTION_PROVIDER", "faster-whisper").lower(),
            whisper_model=_env("WHISPER_MODEL", "small"),
            whisper_device=_env("WHISPER_DEVICE", "auto"),
            transcription_base_url=_env("TRANSCRIPTION_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            transcription_api_key=_env("TRANSCRIPTION_API_KEY"),
            transcription_model=_env("TRANSCRIPTION_MODEL", "whisper-1"),
            llm_base_url=_env("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=_env("LLM_API_KEY"),
            llm_model=_env("LLM_MODEL", ""),
            video_encoder=_env("VIDEO_ENCODER", "libx264"),
            ffmpeg_path=_env("FFMPEG_PATH", "ffmpeg"),
            ffprobe_path=_env("FFPROBE_PATH", "ffprobe"),
            caption_font=_env("CAPTION_FONT", "DejaVu Sans"),
            token_ttl_hours=_int("TOKEN_TTL_HOURS", 24 * 7, 1),
            cors_origins=origins,
            metrics_token=_env("METRICS_TOKEN"),
        )

    def with_overrides(self, **changes) -> Settings:
        return replace(self, **changes)


_override: Settings | None = None


@lru_cache(maxsize=1)
def _cached() -> Settings:
    return Settings.from_env()


def get_settings() -> Settings:
    return _override or _cached()


def set_settings_override(settings: Settings | None) -> None:
    """Used by tests and embedding applications."""
    global _override
    _override = settings
