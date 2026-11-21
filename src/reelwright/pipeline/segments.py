"""Finding clip-worthy moments.

Design (original to this project):

1. Group the word stream into *sentences* (punctuation, or a pause when punctuation is missing).
2. Slide a window over the sentences to build *candidates* between ``min_seconds`` and ``max_seconds``.
3. Score every candidate with transparent heuristics (hook, engagement, value, shareability; each 0-100).
4. Optionally let an LLM re-rank the best candidates and write titles/hooks. If the LLM fails, heuristics decide.
5. Remove overlapping picks and snap boundaries to word edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMClient, LLMError
from .transcribe import Transcript, Word

START_PAD = 0.15
END_PAD = 0.35

_HOOK_WORDS = {
    "never", "secret", "mistake", "mistakes", "why", "how", "truth", "biggest", "nobody", "stop", "actually",
    "worst", "best", "wrong", "myth", "free", "easy", "fast", "simple", "warning", "problem", "lesson", "everyone",
}
_QUESTION_WORDS = {"what", "why", "how", "when", "who", "which", "can", "do", "does", "is", "are", "should", "would", "could"}
_EMOTION_WORDS = {
    "love", "hate", "crazy", "insane", "unbelievable", "amazing", "terrible", "incredible", "shocked", "wow",
    "scary", "afraid", "angry", "happy", "excited", "proud", "regret", "dream", "fear", "laugh",
}
_VALUE_WORDS = {"because", "therefore", "step", "first", "second", "third", "tip", "example", "means", "so", "reason", "rule", "key", "learn"}


@dataclass
class Sentence:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Candidate:
    id: int
    start: float
    end: float
    words: list[Word]
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class ClipPlan:
    start: float
    end: float
    title: str
    hook: str
    reason: str
    scores: dict[str, float]


def split_sentences(words: list[Word], pause: float = 0.8, max_words: int = 28) -> list[Sentence]:
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        current.append(word)
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = bool(re.search(r"[.!?…]['\")\]]*$", word.text))
        long_pause = nxt is not None and nxt.start - word.end > pause
        if ends or long_pause or len(current) >= max_words or nxt is None:
            sentences.append(current)
            current = []
    return [Sentence(s) for s in sentences if s]


def build_candidates(sentences: list[Sentence], min_seconds: float, max_seconds: float) -> list[Candidate]:
    seen: set[tuple[int, int]] = set()
    out: list[Candidate] = []
    for i in range(len(sentences)):
        ends = []
        for j in range(i, len(sentences)):
            length = sentences[j].end - sentences[i].start
            if length > max_seconds:
                break
            if length >= min_seconds:
                ends.append(j)
        # Keep the shortest, a middle and the longest valid window for this start.
        picks = {ends[0], ends[len(ends) // 2], ends[-1]} if ends else set()
        for j in sorted(picks):
            if (i, j) in seen:
                continue
            seen.add((i, j))
            words = [w for s in sentences[i : j + 1] for w in s.words]
            out.append(Candidate(id=len(out), start=sentences[i].start, end=sentences[j].end, words=words))
    return out


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def heuristic_scores(candidate: Candidate, sentences_in: list[Sentence] | None = None) -> dict[str, float]:
    words = [re.sub(r"[^\w']", "", w.text).lower() for w in candidate.words]
    words = [w for w in words if w]
    total = max(len(words), 1)
    text = candidate.text
    first = (sentences_in[0].text if sentences_in else text.split(".")[0]).strip()
    first_tokens = [re.sub(r"[^\w']", "", t).lower() for t in first.split()]

    hook = 20.0
    if first.endswith("?") or (first_tokens and first_tokens[0] in _QUESTION_WORDS):
        hook += 30
    if re.search(r"\d", first):
        hook += 15
    hook += 12 * min(2, sum(t in _HOOK_WORDS for t in first_tokens))
    if 3 <= len(first_tokens) <= 14:
        hook += 15

    rate = total / max(candidate.duration, 1.0)
    pause_total = sum(max(0.0, b.start - a.end) for a, b in zip(candidate.words, candidate.words[1:], strict=False))
    pause_ratio = pause_total / max(candidate.duration, 1.0)
    engagement = 100.0 - abs(rate - 2.8) * 22 - pause_ratio * 120
    engagement += 4 * min(5, text.count("!") + text.count("?"))

    diversity = len(set(words)) / total
    value = 25 + diversity * 45 + 8 * min(3, sum(w in _VALUE_WORDS for w in words))

    share = 20.0 + 9 * min(4, sum(w in _EMOTION_WORDS for w in words)) + (10 if re.search(r"\d", text) else 0)
    share += 10 if text.rstrip().endswith((".", "!", "?")) else 0
    share += 8 if '"' in text or "“" in text else 0

    scores = {k: round(_clamp(v), 1) for k, v in {"hook": hook, "engagement": engagement, "value": value, "shareability": share}.items()}
    scores["overall"] = round(0.35 * scores["hook"] + 0.25 * scores["engagement"] + 0.2 * scores["value"] + 0.2 * scores["shareability"], 1)
    return scores


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(min(a[1] - a[0], b[1] - b[0]), 1e-6)


def pick_non_overlapping(items: list[tuple[float, float, float]], limit: int, max_overlap: float = 0.3) -> list[int]:
    """Greedy selection by descending score. ``items`` are ``(score, start, end)``; returns kept indexes."""
    order = sorted(range(len(items)), key=lambda i: items[i][0], reverse=True)
    kept: list[int] = []
    for i in order:
        if all(_overlap((items[i][1], items[i][2]), (items[k][1], items[k][2])) <= max_overlap for k in kept):
            kept.append(i)
        if len(kept) >= limit:
            break
    return kept


def _title_from(text: str, limit: int = 60) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].strip(" .")
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"
    return sentence[:1].upper() + sentence[1:] if sentence else "Clip"


def _snap(candidate: Candidate, media_duration: float) -> tuple[float, float]:
    return max(0.0, candidate.start - START_PAD), min(media_duration, candidate.end + END_PAD)


SYSTEM_PROMPT = (
    "You are an editor who picks the most engaging moments of a long video for short vertical clips. "
    "Each candidate is a self-contained span of a transcript. Prefer spans that open with a hook, "
    "make one clear point, and end cleanly. Reply with JSON only."
)


def _llm_rank(llm: LLMClient, candidates: list[Candidate], limit: int) -> dict[int, dict[str, Any]]:
    listing = "\n".join(
        f"[{c.id}] ({c.duration:.0f}s) {c.text[:700]}" for c in candidates
    )
    user = (
        f"Choose up to {limit} candidates. For each give: id, title (max 60 chars, no clickbait lies), hook (the opening "
        "line for an on-screen caption, max 70 chars), reason (one sentence), and scores hook/engagement/value/shareability "
        'as integers 0-100. Respond as {"clips": [{"id": 0, "title": "", "hook": "", "reason": "", "scores": {}}]}.\n\n'
        f"Candidates:\n{listing}"
    )
    data = llm.complete_json(SYSTEM_PROMPT, user)
    items = data.get("clips", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise LLMError("The model reply had no clip list.")
    valid = {c.id for c in candidates}
    ranked: dict[int, dict[str, Any]] = {}
    for item in items:
        try:
            cid = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid in valid and cid not in ranked:
            ranked[cid] = item
    if not ranked:
        raise LLMError("The model picked no valid candidates.")
    return ranked


def _coerce_score(value: Any, fallback: float) -> float:
    try:
        return round(_clamp(float(value)), 1)
    except (TypeError, ValueError):
        return fallback


def select_clips(
    transcript: Transcript,
    media_duration: float,
    *,
    max_clips: int = 5,
    min_seconds: float = 15.0,
    max_seconds: float = 60.0,
    llm: LLMClient | None = None,
) -> list[ClipPlan]:
    """Return up to ``max_clips`` non-overlapping clip plans, best first."""
    if min_seconds >= max_seconds:
        raise ValueError("min_seconds must be smaller than max_seconds")
    sentences = split_sentences(transcript.words)
    candidates = build_candidates(sentences, min_seconds, max_seconds)
    if not candidates:
        return _fallback_clip(transcript, media_duration, min_seconds, max_seconds)

    for c in candidates:
        c.scores = heuristic_scores(c, [s for s in sentences if c.start <= s.start < c.end])
    pool_size = max_clips * 3 + 6
    ranked = sorted(candidates, key=lambda c: c.scores["overall"], reverse=True)
    keep = pick_non_overlapping([(c.scores["overall"], c.start, c.end) for c in ranked], pool_size, max_overlap=0.5)
    pool = [ranked[i] for i in keep]

    llm_info: dict[int, dict[str, Any]] = {}
    if llm is not None:
        try:
            llm_info = _llm_rank(llm, pool, max_clips * 2)
        except (LLMError, ValueError):
            llm_info = {}

    scored: list[tuple[float, Candidate, dict[str, Any]]] = []
    for c in pool:
        info = llm_info.get(c.id)
        if llm_info and info is None:
            continue
        scores = dict(c.scores)
        if info:
            given = info.get("scores") or {}
            for key in ("hook", "engagement", "value", "shareability"):
                scores[key] = _coerce_score(given.get(key), scores[key])
            llm_overall = sum(scores[k] for k in ("hook", "engagement", "value", "shareability")) / 4
            scores["overall"] = round(0.7 * llm_overall + 0.3 * c.scores["overall"], 1)
        scored.append((scores["overall"], c, {"scores": scores, "info": info or {}}))

    picks = pick_non_overlapping([(s, c.start, c.end) for s, c, _ in scored], max_clips)
    plans: list[ClipPlan] = []
    for i in picks:
        _, c, extra = scored[i]
        info = extra["info"]
        start, end = _snap(c, media_duration)
        title = str(info.get("title") or "").strip()[:60] or _title_from(c.text)
        hook = str(info.get("hook") or "").strip()[:70] or _title_from(c.text, 70)
        reason = str(info.get("reason") or "").strip() or "Selected by transcript heuristics (hook, pacing, clarity)."
        plans.append(ClipPlan(round(start, 2), round(end, 2), title, hook, reason, extra["scores"]))
    return plans


def _fallback_clip(transcript: Transcript, media_duration: float, min_seconds: float, max_seconds: float) -> list[ClipPlan]:
    """Not enough speech for a candidate window: offer one starter clip from the beginning."""
    end = min(media_duration, max_seconds)
    if end < 1.0:
        return []
    text = transcript.text
    title = _title_from(text) if text else "Starter clip"
    scores = {"hook": 0.0, "engagement": 0.0, "value": 0.0, "shareability": 0.0, "overall": 0.0}
    return [ClipPlan(0.0, round(end, 2), title, title, "Starter clip: too little speech to find a stronger moment.", scores)]
