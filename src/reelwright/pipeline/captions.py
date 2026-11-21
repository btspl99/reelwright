"""Word-synced captions as ASS subtitles (rendered by ffmpeg's libass)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transcribe import Word

PLAY_W, PLAY_H = 1080, 1920


@dataclass(frozen=True)
class CaptionStyle:
    name: str
    font_size: int
    primary: str  # ASS colour &HAABBGGRR
    highlight: str | None
    outline: str
    outline_px: int
    box: bool
    bold: bool = True
    scale_highlight: int = 100  # percent


STYLES: dict[str, CaptionStyle] = {
    "classic": CaptionStyle("classic", 74, "&H00FFFFFF", None, "&H00000000", 5, False),
    "pop": CaptionStyle("pop", 78, "&H00FFFFFF", "&H0000D7FF", "&H00000000", 6, False, scale_highlight=112),
    "boxed": CaptionStyle("boxed", 68, "&H00FFFFFF", "&H0040E070", "&HA0000000", 14, True),
}
STYLE_NAMES = tuple(STYLES) + ("none",)


def ass_time(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def escape_text(text: str) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    return re.sub(r"[\r\n]+", " ", text).strip()


def group_words(words: list[Word], max_words: int = 4, max_chars: int = 24, gap: float = 0.7) -> list[list[Word]]:
    groups: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        too_long = current and (len(current) >= max_words or sum(len(x.text) + 1 for x in current) + len(w.text) > max_chars)
        paused = current and w.start - current[-1].end > gap
        if too_long or paused:
            groups.append(current)
            current = []
        current.append(w)
        if re.search(r"[.!?]$", w.text):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _line_break(plain: list[str], pieces: list[str] | None = None) -> str:
    """Join words, breaking into two balanced lines when the text is long. ``pieces`` may carry override tags."""
    pieces = pieces or plain
    joined = " ".join(plain)
    if len(joined) <= 16 or len(plain) < 3:
        return " ".join(pieces)
    half = len(joined) / 2
    run = 0
    for i, t in enumerate(plain):
        run += len(t) + 1
        if run >= half:
            if i + 1 >= len(plain):
                break
            return " ".join(pieces[: i + 1]) + "\\N" + " ".join(pieces[i + 1 :])
    return " ".join(pieces)


def _style_line(name: str, font: str, st: CaptionStyle, *, align: int, margin_v: int) -> str:
    border_style = 3 if st.box else 1
    back = st.outline if st.box else "&H64000000"
    return (
        f"Style: {name},{font},{st.font_size},{st.primary},{st.primary},{st.outline},{back},"
        f"{-1 if st.bold else 0},0,0,0,100,100,0,0,{border_style},{st.outline_px},0,{align},60,60,{margin_v},1"
    )


def build_ass(
    words: list[Word],
    clip_start: float,
    clip_end: float,
    *,
    style: str = "pop",
    font: str = "DejaVu Sans",
    title: str | None = None,
    title_seconds: float = 3.0,
) -> str:
    """Return a complete ASS document. ``words`` carry source timestamps; events are shifted to clip time."""
    st = STYLES.get(style)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {PLAY_W}", f"PlayResY: {PLAY_H}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
    ]
    base = st or STYLES["classic"]
    lines.append(_style_line("Cap", font, base, align=2, margin_v=430))
    hook_style = CaptionStyle("hook", 62, "&H00FFFFFF", None, "&HB4000000", 18, True)
    lines.append(_style_line("Hook", font, hook_style, align=8, margin_v=300))
    lines += ["", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]

    duration = clip_end - clip_start
    if title:
        end = min(title_seconds, duration)
        wrapped = _wrap_title(escape_text(title))
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,{{\\fad(250,250)}}{wrapped}")

    if st is not None:
        clip_words = [w for w in words if w.end > clip_start and w.start < clip_end]
        for group in group_words(clip_words):
            rel = [Word(escape_text(w.text), max(w.start - clip_start, 0.0), min(w.end - clip_start, duration)) for w in group]
            rel = [w for w in rel if w.text]
            if not rel:
                continue
            if st.highlight is None:
                text = _line_break([w.text for w in rel])
                lines.append(f"Dialogue: 0,{ass_time(rel[0].start)},{ass_time(max(rel[-1].end, rel[0].start + 0.1))},Cap,,0,0,0,,{text}")
                continue
            for i, _active in enumerate(rel):
                start = rel[i].start
                end = rel[i + 1].start if i + 1 < len(rel) else max(rel[-1].end, start + 0.1)
                if end <= start:
                    end = start + 0.05
                pieces = []
                for j, w in enumerate(rel):
                    if j == i:
                        scale = st.scale_highlight
                        pieces.append(f"{{\\c{st.highlight}\\fscx{scale}\\fscy{scale}}}{w.text}{{\\c{st.primary}\\fscx100\\fscy100}}")
                    else:
                        pieces.append(w.text)
                text = _line_break([w.text for w in rel], pieces)
                lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def _wrap_title(title: str, width: int = 22) -> str:
    out, line = [], ""
    for word in title.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return "\\N".join(out[:3])
