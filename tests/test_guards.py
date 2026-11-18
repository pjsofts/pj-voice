from pjvoice.engine.guards import NO_PROGRESS_NOTE, NoProgressTracker, RepeatSuppressor


def stream(text: str, size: int) -> str:
    s = RepeatSuppressor()
    out = "".join(s.push(text[i:i + size]) for i in range(0, len(text), size))
    return out + s.flush()


def test_drops_verbatim_repeat_any_chunking():
    once = "Do you want all six appointments cancelled?"
    for size in (1, 3, 7, 100):
        assert stream(once + once, size).strip() == once


def test_keeps_genuine_continuation():
    text = "Sure, I can help with that. Do you want the morning or the afternoon?"
    for size in (1, 4, 50):
        assert stream(text, size) == text


def test_keeps_prefix_lookalike():
    text = "Thanks for calling today. Thanks for calling back so quickly too."
    assert stream(text, 2) == text


def test_short_pleasantries_not_watched():
    assert stream("No problem. No problem.", 1) == "No problem. No problem."


def test_no_progress_only_on_identical_args_and_result():
    t = NoProgressTracker()
    assert t.annotate("a", {"x": 1}, "r") == "r"
    assert t.annotate("a", {"x": 1}, "r2") == "r2"  # result changed: fine
    assert t.annotate("a", {"x": 1}, "r2").endswith(NO_PROGRESS_NOTE)
    assert t.annotate("a", {"x": 2}, "r2") == "r2"
