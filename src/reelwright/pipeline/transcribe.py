"""Speech-to-text with word-level timestamps. Providers: local faster-whisper, OpenAI-compatible API, sidecar JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from ..config import Settings
from .media import MediaError, run


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    language: str = ""
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def duration(self) -> float:
        return self.words[-1].end if self.words else 0.0

    def slice(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if w.end > start and w.start < end]

    def to_dict(self) -> dict:
        return {"language": self.language, "words": [asdict(w) for w in self.words]}

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        words = [
            Word(str(w["text"]), float(w["start"]), float(w["end"]))
            for w in data.get("words", [])
            if str(w.get("text", "")).strip()
        ]
        words.sort(key=lambda w: w.start)
        return cls(language=str(data.get("language", "")), words=words)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path) -> Transcript:
        return cls.from_dict(json.loads(path.read_text()))


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: str | None = None) -> Transcript: ...


class FasterWhisperTranscriber:
    """Local transcription with faster-whisper (``pip install reelwright[whisper]``)."""

    _model = None
    _key: tuple | None = None

    def __init__(self, settings: Settings):
        self.settings = settings

    def _load(self):
        key = (self.settings.whisper_model, self.settings.whisper_device)
        if FasterWhisperTranscriber._model is None or FasterWhisperTranscriber._key != key:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError as exc:
                raise TranscriptionError("faster-whisper is not installed (pip install reelwright[whisper]).") from exc
            device = self.settings.whisper_device
            compute = "int8" if device in ("cpu", "auto") else "float16"
            FasterWhisperTranscriber._model = WhisperModel(self.settings.whisper_model, device=device, compute_type=compute)
            FasterWhisperTranscriber._key = key
        return FasterWhisperTranscriber._model

    def transcribe(self, audio_path: Path, language: str | None = None) -> Transcript:
        model = self._load()
        segments, info = model.transcribe(str(audio_path), language=language or None, word_timestamps=True, vad_filter=True)
        words = [
            Word(w.word.strip(), float(w.start), float(w.end))
            for seg in segments
            for w in (seg.words or [])
            if w.word.strip()
        ]
        return Transcript(language=info.language or "", words=words)


class OpenAICompatTranscriber:
    """``POST {base}/audio/transcriptions`` (OpenAI, Groq, local whisper servers). Audio is sent in chunks."""

    CHUNK_SECONDS = 600

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=300)

    def _chunks(self, audio_path: Path) -> list[tuple[Path, float]]:
        out_dir = audio_path.parent / "chunks"
        out_dir.mkdir(exist_ok=True)
        pattern = out_dir / "chunk_%04d.ogg"
        run(
            [self.settings.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio_path), "-vn", "-c:a", "libopus", "-b:a", "24k",
             "-f", "segment", "-segment_time", str(self.CHUNK_SECONDS), "-reset_timestamps", "1", str(pattern)],
            timeout=3600,
        )
        files = sorted(out_dir.glob("chunk_*.ogg"))
        return [(f, i * float(self.CHUNK_SECONDS)) for i, f in enumerate(files)]

    def transcribe(self, audio_path: Path, language: str | None = None) -> Transcript:
        if not self.settings.transcription_api_key:
            raise TranscriptionError("REELWRIGHT_TRANSCRIPTION_API_KEY is not set.")
        headers = {"Authorization": f"Bearer {self.settings.transcription_api_key}"}
        words: list[Word] = []
        detected = ""
        try:
            chunks = self._chunks(audio_path)
        except MediaError as exc:
            raise TranscriptionError(str(exc)) from exc
        for path, offset in chunks:
            data = [("model", self.settings.transcription_model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
            if language:
                data.append(("language", language))
            with path.open("rb") as handle:
                response = self.client.post(
                    f"{self.settings.transcription_base_url}/audio/transcriptions",
                    headers=headers, data=data, files={"file": (path.name, handle, "audio/ogg")},
                )
            if response.status_code >= 400:
                raise TranscriptionError(f"Transcription API error {response.status_code}: {response.text[:200]}")
            body = response.json()
            detected = detected or str(body.get("language", ""))
            for w in body.get("words", []) or []:
                text = str(w.get("word", "")).strip()
                if text:
                    words.append(Word(text, float(w["start"]) + offset, float(w["end"]) + offset))
        return Transcript(language=detected, words=words)


class SidecarTranscriber:
    """Reads ``<media>.transcript.json`` next to the source. Useful offline, for tests and for corrected transcripts."""

    def __init__(self, media_path: Path):
        self.media_path = media_path

    def transcribe(self, audio_path: Path, language: str | None = None) -> Transcript:
        sidecar = self.media_path.with_suffix(self.media_path.suffix + ".transcript.json")
        if not sidecar.exists():
            raise TranscriptionError(f"No sidecar transcript found at {sidecar.name}.")
        return Transcript.load(sidecar)


def get_transcriber(settings: Settings, media_path: Path) -> Transcriber:
    # A sidecar transcript always wins: it is explicit user input.
    sidecar = media_path.with_suffix(media_path.suffix + ".transcript.json")
    if sidecar.exists():
        return SidecarTranscriber(media_path)
    provider = settings.transcription_provider
    if provider in ("faster-whisper", "whisper", "local"):
        return FasterWhisperTranscriber(settings)
    if provider in ("openai", "api"):
        return OpenAICompatTranscriber(settings)
    if provider == "sidecar":
        return SidecarTranscriber(media_path)
    raise TranscriptionError(f"Unknown or disabled transcription provider: {provider!r}")
