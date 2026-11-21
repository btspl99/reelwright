from reelwright.pipeline import captions, reframe, segments
from reelwright.pipeline.transcribe import Transcript, Word


def make_words(text, start=0.0, step=0.4):
    out, t = [], start
    for w in text.split():
        out.append(Word(w, t, t + step * 0.9))
        t += step
    return out


def test_split_sentences_on_pause():
    words = make_words("hello there friend") + make_words("second sentence here", start=5.0)
    assert len(segments.split_sentences(words)) >= 2


def test_pick_non_overlapping():
    items = [(0.9, 0, 10), (0.8, 5, 15), (0.7, 20, 30)]  # (score, start, end)
    assert segments.pick_non_overlapping(items, 3) == [0, 2]


def test_select_clips_respects_bounds_and_overlap():
    text = ("Here is the surprising truth nobody tells you about saving money. " * 1 +
            "You should always automate it because small habits compound over time. ") * 12
    words = make_words(text)
    duration = words[-1].end + 2
    plans = segments.select_clips(Transcript("en", words), duration, max_clips=3, min_seconds=10, max_seconds=30)
    assert 1 <= len(plans) <= 3
    for p in plans:
        assert 10 - 1 <= p.end - p.start <= 30 + 1 and 0 <= p.start < p.end <= duration
    ordered = sorted(plans, key=lambda p: p.start)
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert a.end <= b.start + 0.5 * (a.end - a.start)


def test_group_words_limits():
    groups = captions.group_words(make_words("a b c d e f g h i j"), max_words=3)
    assert all(len(g) <= 3 for g in groups) and sum(len(g) for g in groups) == 10


def test_ass_output_and_escaping():
    ass = captions.build_ass(make_words("Hello {bad} world"), 0.0, 5.0, style="pop", title="Big {Title}")
    assert "[Events]" in ass and "Dialogue:" in ass
    assert "{bad}" not in ass.replace("{\\", "")  # user braces must not become override tags


def test_ass_none_style_has_no_captions():
    ass = captions.build_ass(make_words("hello world"), 0.0, 5.0, style="none")
    assert "hello" not in ass


def test_smooth_path_deadzone_and_speed():
    samples = [reframe.Sample(t=i * 0.5, cx=960 + (5 if i % 2 else -5)) for i in range(20)]
    pts = reframe.smooth_path(samples, 1920, 608)
    xs = [x for _, x in pts]
    assert max(xs) - min(xs) < 1.0  # jitter inside the dead zone is ignored
    assert all(0 <= x <= 1920 - 608 for x in xs)


def test_simplify_and_expression():
    pts = [(i * 0.5, 100 + i) for i in range(30)]
    simple = reframe.simplify(pts, tolerance=2.0)
    assert 2 <= len(simple) < len(pts)
    expr = reframe.x_expression(simple)
    assert "t" in expr and expr.count("(") == expr.count(")")
    assert reframe.x_expression([(0.0, 50.0)]).replace(" ", "") in ("50", "50.0", "50.00")
