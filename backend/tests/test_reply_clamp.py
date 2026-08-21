"""_ReplyClamp — the voice-mode prose cap that must NEVER cut a <nova_action>.

The phone's "truncated reply" bug: voice turns are clamped at
VOICE_REPLY_CHAR_CAP chars, and the old clamp counted the trailing
<nova_action> block against the cap — cutting the JSON mid-block, which
leaked a raw fragment on screen and killed the confirm-card parser. The
rewritten clamp caps SPOKEN PROSE only; action blocks pass verbatim.
"""

from __future__ import annotations

import random

from app.main import _ReplyClamp

ACTION = '<nova_action>{"tool":"run_workflow","command":"research","prompt":"smart glasses"}</nova_action>'


def _stream(clamp: _ReplyClamp, text: str, chunk: int) -> str:
    out = []
    for i in range(0, len(text), chunk):
        out.append(clamp.take(text[i:i + chunk]))
    out.append(clamp.flush())
    return "".join(out)


def test_passthrough_when_no_limit():
    c = _ReplyClamp(None)
    assert c.take("hello " * 500) == "hello " * 500
    assert c.flush() == ""


def test_prose_only_is_capped():
    c = _ReplyClamp(10)
    assert _stream(c, "abcdefghijKLMNOP", 3) == "abcdefghij"


def test_action_block_survives_cap_intact():
    text = ("x" * 590) + ACTION          # prose alone nearly fills the cap
    for chunk in (1, 7, 64, 1000):
        got = _stream(_ReplyClamp(600), text, chunk)
        assert got.count("<nova_action>") == 1
        assert got.endswith("</nova_action>")
        assert ACTION in got             # byte-exact, never trimmed


def test_action_block_exempt_even_when_prose_already_over():
    text = ("y" * 900) + ACTION          # prose overflows the cap by 300
    got = _stream(_ReplyClamp(600), text, 13)
    assert got.startswith("y" * 600)
    assert ACTION in got
    assert "y" * 601 not in got          # overflow prose dropped


def test_split_tag_across_chunks():
    # The opening tag split at every possible boundary must still be detected.
    text = "hello " + ACTION + " bye"
    for cut in range(1, len(ACTION)):
        c = _ReplyClamp(6)
        got = c.take(text[:6 + cut]) + c.take(text[6 + cut:]) + c.flush()
        assert ACTION in got, f"tag lost at split {cut}"
        assert "bye" not in got          # trailing prose past cap dropped


def test_prose_after_action_still_counts_against_cap():
    text = "ab" + ACTION + "cdefgh"
    got = _stream(_ReplyClamp(4), text, 5)
    assert got == "ab" + ACTION + "cd"


def test_unterminated_action_is_dropped_at_flush():
    # A genuinely truncated provider stream: never emit a half block.
    text = "hi " + ACTION[: len(ACTION) // 2]
    got = _stream(_ReplyClamp(600), text, 9)
    assert got == "hi "


def test_angle_bracket_prose_is_not_swallowed():
    text = "use a < b and <novel ideas> here"
    got = _stream(_ReplyClamp(600), text, 4)
    assert got == text


def test_random_chunking_never_corrupts_the_block():
    rng = random.Random(42)
    text = ("p" * 550) + ACTION + ("q" * 50)
    for _ in range(50):
        c = _ReplyClamp(600)
        out, i = [], 0
        while i < len(text):
            j = min(len(text), i + rng.randint(1, 40))
            out.append(c.take(text[i:j]))
            i = j
        out.append(c.flush())
        got = "".join(out)
        assert ACTION in got
