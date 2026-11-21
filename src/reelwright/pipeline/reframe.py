"""Turning a landscape frame into a 9:16 crop that follows the speaker.

Pipeline: sample frames -> detect faces -> choose one face per sample -> smooth the crop position with a dead-zone
and a speed limit (so the virtual camera does not jitter) -> simplify to a few key points -> emit an ffmpeg
expression ``x(t)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

TARGET_W, TARGET_H = 1080, 1920


class FaceDetector(Protocol):
    def detect(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Return ``(x, y, w, h)`` boxes."""


class HaarFaceDetector:
    """OpenCV's bundled Haar cascade: dependency-free and CPU-only. Swap in a stronger detector via the protocol."""

    def __init__(self) -> None:
        self._cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))

    def detect(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        scale = 480.0 / max(frame_bgr.shape[1], 1)
        small = cv2.resize(frame_bgr, None, fx=scale, fy=scale) if scale < 1 else frame_bgr
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        inv = 1.0 / scale if scale < 1 else 1.0
        return [(int(x * inv), int(y * inv), int(w * inv), int(h * inv)) for x, y, w, h in faces]


@dataclass(frozen=True)
class Sample:
    t: float  # seconds from the start of the clip
    cx: float | None  # face centre x in source pixels, or None when no face was found


@dataclass(frozen=True)
class ReframePlan:
    layout: str  # fill | fit_blur | fit
    crop_w: int
    crop_h: int
    x_points: list[tuple[float, float]]  # (t, left edge) key points for layout == fill
    face_ratio: float


def crop_width(src_w: int, src_h: int) -> int:
    """Width of a 9:16 crop that spans the full height, rounded down to an even number."""
    return min(src_w, int(src_h * 9 / 16) // 2 * 2)


def sample_faces(
    src: Path, start: float, end: float, detector: FaceDetector, fps: float = 3.0
) -> list[Sample]:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return []
    samples: list[Sample] = []
    prev_cx: float | None = None
    try:
        t = 0.0
        while start + t < end:
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + t) * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            boxes = detector.detect(frame)
            cx = choose_face(boxes, prev_cx)
            samples.append(Sample(t, cx))
            prev_cx = cx if cx is not None else prev_cx
            t += 1.0 / fps
    finally:
        cap.release()
    return samples


def choose_face(boxes: list[tuple[int, int, int, int]], prev_cx: float | None) -> float | None:
    """Prefer large faces, and prefer staying with the previously followed one."""
    if not boxes:
        return None

    def score(box: tuple[int, int, int, int]) -> float:
        x, _, w, h = box
        cx = x + w / 2
        area = w * h
        stick = 0.0 if prev_cx is None else max(0.0, 1.0 - abs(cx - prev_cx) / (4.0 * w)) * area * 0.8
        return area + stick

    x, _, w, _ = max(boxes, key=score)
    return x + w / 2


def smooth_path(
    samples: list[Sample],
    src_w: int,
    crop_w: int,
    *,
    deadzone: float = 0.12,
    max_speed: float = 0.7,
    hold_seconds: float = 1.5,
) -> list[tuple[float, float]]:
    """Left-edge positions per sample. ``deadzone`` and ``max_speed`` are fractions of ``crop_w`` (per second for speed)."""
    if not samples:
        return [(0.0, (src_w - crop_w) / 2)]
    max_left = src_w - crop_w
    cur_center = src_w / 2
    first = next((s.cx for s in samples if s.cx is not None), None)
    if first is not None:
        cur_center = first
    path: list[tuple[float, float]] = []
    last_seen_t = samples[0].t
    last_cx = cur_center
    prev_t = samples[0].t
    for s in samples:
        dt = max(s.t - prev_t, 0.0)
        prev_t = s.t
        if s.cx is not None:
            last_seen_t, last_cx = s.t, s.cx
            target = s.cx
        elif s.t - last_seen_t > hold_seconds:
            target = src_w / 2  # nobody on screen for a while: ease back to the middle
        else:
            target = last_cx
        if abs(target - cur_center) > deadzone * crop_w:
            step = max_speed * crop_w * dt
            delta = target - cur_center
            cur_center += max(-step, min(step, delta)) if dt > 0 else delta
        left = min(max(cur_center - crop_w / 2, 0.0), max_left)
        path.append((s.t, left))
    return path


def simplify(points: list[tuple[float, float]], tolerance: float = 6.0) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on (t, x) with x in pixels and t scaled to pixels at 1 s = 100 px."""
    if len(points) <= 2:
        return points
    pts = np.array([(t * 100.0, x) for t, x in points])

    def rdp(lo: int, hi: int, keep: set[int]) -> None:
        if hi <= lo + 1:
            return
        a, b = pts[lo], pts[hi]
        ab = b - a
        norm = np.hypot(*ab) or 1.0
        dists = np.abs(ab[0] * (pts[lo + 1 : hi, 1] - a[1]) - ab[1] * (pts[lo + 1 : hi, 0] - a[0])) / norm
        idx = int(np.argmax(dists))
        if dists[idx] > tolerance:
            mid = lo + 1 + idx
            keep.add(mid)
            rdp(lo, mid, keep)
            rdp(mid, hi, keep)

    keep = {0, len(points) - 1}
    rdp(0, len(points) - 1, keep)
    return [points[i] for i in sorted(keep)]


def x_expression(points: list[tuple[float, float]]) -> str:
    """ffmpeg expression for the crop's left edge as a function of ``t`` (piecewise linear)."""
    if len(points) == 1 or all(abs(p[1] - points[0][1]) < 0.5 for p in points):
        return f"{points[0][1]:.1f}"
    expr = f"{points[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(points, points[1:], strict=False))):
        span = max(t1 - t0, 1e-3)
        seg = f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{span:.3f}"
        expr = f"if(lt(t\\,{t1:.3f})\\,{seg}\\,{expr})"
    return expr


def plan_reframe(
    src: Path,
    src_w: int,
    src_h: int,
    start: float,
    end: float,
    *,
    layout: str = "auto",
    detector: FaceDetector | None = None,
) -> ReframePlan:
    """Decide how to turn the source into 1080x1920 for the span ``[start, end]``."""
    cw = crop_width(src_w, src_h)
    if src_w <= src_h * 0.8:  # already portrait or near-square-portrait
        return ReframePlan("fit" if layout == "auto" else layout, src_w, src_h, [], 0.0)
    if layout == "fit_blur":
        return ReframePlan("fit_blur", cw, src_h, [], 0.0)
    samples = sample_faces(src, start, end, detector or HaarFaceDetector(), fps=3.0)
    found = sum(1 for s in samples if s.cx is not None)
    ratio = found / len(samples) if samples else 0.0
    if layout == "auto" and ratio < 0.4:
        return ReframePlan("fit_blur", cw, src_h, [], ratio)
    path = smooth_path(samples, src_w, cw)
    return ReframePlan("fill", cw, src_h, simplify(path), ratio)


def filter_graph(plan: ReframePlan, src_w: int, src_h: int) -> str:
    """The video filtergraph (without captions) that produces a 1080x1920 yuv420p frame."""
    if plan.layout == "fill":
        return (
            f"crop={plan.crop_w}:{plan.crop_h}:x='{x_expression(plan.x_points)}':y=0,"
            f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,setsar=1,format=yuv420p"
        )
    if plan.layout == "fit_blur":
        return (
            f"split[a][b];[a]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},boxblur=24:6[bg];[b]scale={TARGET_W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p"
        )
    # fit: portrait source, scale to height and pad
    return (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
    )
