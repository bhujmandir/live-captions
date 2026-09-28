"""What the hall sees must be a whole thought, not half of one.

Saaras emits one `data` message per VAD segment. A speaker who pauses
mid-sentence therefore produces two "finals" that are each a fragment. The
old behaviour translated and displayed each fragment on its own — which is
how a Gujarati sentence gets botched, because Gujarati puts the verb last
and a fragment ending before the verb has no predicate to translate.

`SentenceAssembler` holds fragments until they look like a whole sentence,
then hands the joined text on for translation. These tests pin the four
ways a sentence is allowed to end, and the two ways it must never stall.
"""
import pytest

from live_captions import SentenceAssembler


def mk(**kw) -> SentenceAssembler:
    """A test assembler.

    `min_words=1` by default so the tests below exercise the punctuation
    *mechanism* without the word floor interfering. The floor is a separate
    policy with its own section — see "punctuation is not trustworthy on its
    own" at the bottom, which passes min_words explicitly.
    """
    opts = dict(max_wait_sec=4.0, quiet_sec=1.2, max_chars=220,
                min_words=1, enabled=True)
    opts.update(kw)
    return SentenceAssembler(**opts)


# ── 1 · punctuation closes a sentence ────────────────────────────────────

def test_terminal_punctuation_releases_immediately():
    a = mk()
    assert a.add("ભગવાનની કથાનો આરંભ કરીએ છીએ.", now=0.0) == "ભગવાનની કથાનો આરંભ કરીએ છીએ."


def test_fragment_without_punctuation_is_held():
    a = mk()
    assert a.add("ભગવાનની કથાનો", now=0.0) is None


def test_fragments_join_into_one_sentence():
    a = mk()
    assert a.add("ભગવાનની કથાનો", now=0.0) is None
    assert a.add("આરંભ કરીએ છીએ.", now=0.6) == "ભગવાનની કથાનો આરંભ કરીએ છીએ."


@pytest.mark.parametrize("ender", [".", "!", "?", "।", "॥", '."', ".'", ".”"])
def test_every_sentence_ender_counts(ender):
    """Devanagari danda and double-danda end a sentence as surely as a full
    stop does, and a closing quote after the stop must not hide it."""
    a = mk()
    assert a.add(f"વાત પૂરી{ender}", now=0.0) is not None


def test_a_decimal_point_is_not_a_sentence_end():
    a = mk()
    assert a.add("કિંમત 3.5", now=0.0) is None


# ── 2 · the speaker stopped ──────────────────────────────────────────────

def test_quiet_gap_releases_an_unpunctuated_fragment():
    """If nothing more arrives, waiting longer buys nothing — show it."""
    a = mk()
    a.add("ભગવાનની કથાનો", now=0.0)
    assert a.due(now=0.9) is None          # still within the quiet window
    assert a.due(now=1.3) == "ભગવાનની કથાનો"


def test_quiet_window_restarts_on_each_fragment():
    a = mk()
    a.add("એક", now=0.0)
    a.add("બે", now=1.0)                    # resets the quiet clock
    assert a.due(now=1.9) is None
    assert a.due(now=2.3) == "એક બે"


# ── 3 · the speaker never stops ──────────────────────────────────────────

def test_total_wait_is_capped_so_a_caption_can_never_go_stale():
    """A speaker in full flow yields no punctuation and no gap. The hall
    still gets a caption — bounded staleness beats a perfect sentence."""
    a = mk(max_wait_sec=4.0)
    a.add("એક", now=0.0)
    for t in (1.0, 2.0, 3.0):
        assert a.add("વધુ", now=t) is None
    assert a.add("વધુ", now=4.1) == "એક વધુ વધુ વધુ વધુ"


def test_a_long_buffer_is_released_before_it_becomes_a_wall_of_text():
    a = mk(max_chars=40)
    a.add("x" * 30, now=0.0)
    out = a.add("y" * 20, now=0.2)
    assert out is not None and len(out) >= 40


# ── 4 · never lose the last words of the katha ───────────────────────────

def test_flush_returns_whatever_is_held():
    a = mk()
    a.add("છેલ્લા શબ્દો", now=0.0)
    assert a.flush() == "છેલ્લા શબ્દો"
    assert a.flush() is None       # and only once


def test_flush_on_an_empty_buffer_is_silent():
    assert mk().flush() is None


# ── 5 · bookkeeping ──────────────────────────────────────────────────────

def test_releasing_clears_the_buffer():
    a = mk()
    a.add("પહેલું વાક્ય.", now=0.0)
    assert a.add("બીજું", now=1.0) is None
    assert a.due(now=2.5) == "બીજું"

def test_blank_fragments_never_start_a_sentence():
    """A blank must not start the quiet clock — otherwise an empty caption
    is emitted 1.2s later."""
    a = mk()
    assert a.add("   ", now=0.0) is None
    assert a.due(now=5.0) is None


def test_whitespace_between_fragments_is_normalised():
    a = mk()
    a.add("  એક  ", now=0.0)
    assert a.add(" બે. ", now=0.5) == "એક બે."


# ── 6 · the off switch ───────────────────────────────────────────────────

def test_disabled_passes_every_fragment_straight_through():
    """The old per-utterance behaviour must remain reachable in one env var,
    so a bad night at the mandir can be reverted without a code change."""
    a = mk(enabled=False)
    assert a.add("ભગવાનની કથાનો", now=0.0) == "ભગવાનની કથાનો"
    assert a.due(now=99.0) is None
    assert a.flush() is None


# ── 7 · end to end, through the supervisor ───────────────────────────────
#
# The unit tests above pin the assembler. These pin the thing that actually
# matters: what reaches the browser when the speech service splits one
# sentence across two VAD segments.

import live_captions
from tests.fakes import (FakeSarvamClient, FakeSession, room, scripted_audio,
                         speech)


@pytest.fixture(autouse=True)
def sentence_mode_on(monkeypatch):
    """Run these against the values that actually ship.

    conftest's `documented_defaults` already pins every env-derived constant
    to its shipped value, so this only has to force the mode on. It used to
    pin 1.2s/4.0s — the two numbers a real reading later disproved — which
    meant the shipped 2.5s/8.0s were never exercised end to end. A test that
    passes on numbers nobody runs is not evidence about the product.

    A test that is *about* one of these values overrides it explicitly.
    """
    monkeypatch.setattr(live_captions, "SENTENCE_MODE", True)
    assert live_captions.SENTENCE_QUIET_SEC == 2.5
    assert live_captions.SENTENCE_MAX_WAIT_SEC == 8.0
    assert live_captions.SENTENCE_MIN_WORDS == 5


async def run_supervisor(*, audio, broadcaster, state, client):
    stop_event = __import__("asyncio").Event()
    await live_captions.sarvam_loop(
        audio, broadcaster, False, stop_event, state,
        sarvam_cfg={"model": "saaras:v3"}, client=client,
    )


async def test_one_sentence_split_across_two_segments_is_shown_once(
        clock, broadcaster, state):
    """The fault this whole feature exists to fix.

    The speaker draws breath mid-sentence, so Saaras finalises twice. The
    hall must see one whole sentence, not two half ones.
    """
    async def speak_in_two_halves(n, session):
        if n == 1:
            session.emit_transcript("we begin the katha")
        if n == 2:
            session.emit_transcript("of the Lord today.")

    session = FakeSession(clock, on_frame=speak_in_two_halves)
    client = FakeSarvamClient(sessions=[session])

    await run_supervisor(
        audio=scripted_audio([speech()] * 4, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    finals = [m["text"] for m in broadcaster.of_type("final")]
    assert finals == ["we begin the katha of the Lord today."]


async def test_a_sentence_left_hanging_is_still_shown(clock, broadcaster, state):
    """The speaker trails off without a full stop, then stops talking. The
    words must still reach the screen — held for the quiet window, not lost."""
    async def trail_off(n, session):
        if n == 1:
            session.emit_transcript("and so the story goes")

    session = FakeSession(clock, on_frame=trail_off)
    client = FakeSarvamClient(sessions=[session])

    # Silence long enough to pass CAPTION_SENTENCE_QUIET_SEC (1.2s) — each
    # room frame is one FRAME_SEC of virtual time.
    await run_supervisor(
        audio=scripted_audio([speech()] + [room()] * 10, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    finals = [m["text"] for m in broadcaster.of_type("final")]
    assert finals == ["and so the story goes"]


# ── 8 · punctuation is not trustworthy on its own ────────────────────────
#
# Measured on a real Vachanamrut reading through the mandir mixer: 90 of 138
# segments ended in punctuation, and 49 of those were two words or fewer —
# `સત્ય.` ("The truth."), `ભ્રમ.` ("A delusion."), `માથ.` ("The head.").
# Saaras punctuates a VAD segment, not a sentence. Releasing on a full stop
# alone therefore leaves the fragmentation almost exactly as it was, which is
# the fault this feature exists to fix.

def test_punctuation_on_a_two_word_fragment_does_not_end_a_sentence():
    a = mk(min_words=5)
    assert a.add("સત્ય.", now=0.0) is None
    assert a.add("સત્સંગ કર્યો હોય.", now=1.0) is None


def test_punctuation_releases_once_there_are_enough_words():
    a = mk(min_words=5)
    assert a.add("સત્ય.", now=0.0) is None
    out = a.add("એ ભગવાનનો તથા સંતનો અવગુણ આવે.", now=1.0)
    assert out == "સત્ય. એ ભગવાનનો તથા સંતનો અવગુણ આવે."


def test_a_short_line_is_still_released_by_the_clock():
    """A genuinely short sentence — a question, an exclamation — must not be
    held forever just because it is under the word floor."""
    a = mk(min_words=5, quiet_sec=1.2)
    assert a.add("શા માટે?", now=0.0) is None
    assert a.due(now=1.5) == "શા માટે?"


# ── 9 · the ceiling is about the sabha's eyes, not the wire ──────────────
#
# `SENTENCE_MAX_CHARS` shipped at 220 for the first katha. 220 characters is
# roughly 35-40 words, which needs about 13 seconds to read at normal subtitle
# reading speed — and captions arrive about every 4.0s on this speaker. So the
# shipped default permitted captions that could not be read before they were
# replaced, and the owner reported exactly that from the live stream as text
# "cutting off".
#
# No display timer can fix it: a caption that needs 13s in a 4s slot is
# unreadable however long it is held, because holding it longer only puts the
# NEXT caption further behind. Only a shorter caption fixes it.
#
# This test exists so raising the ceiling back is a decision someone makes on
# purpose, with this reasoning in front of them, rather than a number nudged up
# to stop sentences being split.

# 🔴 51, not the 90 this test first used. Measured in Chrome against the
# shipped default layout — a 1280px caption area at 56px — rather than
# inherited from the mandir PC's own layout, which is where the 90 came from
# and why it was wrong here.
CHARS_PER_LINE = 51
DISPLAYED_LINES = 2
CHARS_PER_BLOCK = CHARS_PER_LINE * DISPLAYED_LINES   # ~102
# A caption may span at most two displayed blocks. The overlay splits rather
# than truncates now, so this is a bound on how long one caption occupies the
# screen — two blocks is about 4s at the block cadence, roughly one caption
# arrival interval.
MAX_BLOCKS_PER_CAPTION = 2


def test_the_shipped_ceiling_is_no_more_than_two_blocks_can_hold():
    import live_captions

    # The SHIPPED default, not the fixture's pin and not a value this test
    # supplies. Calling _env_float() here with a default of our own would
    # assert nothing about what a deployment runs.
    shipped = live_captions.SENTENCE_MAX_CHARS_DEFAULT
    ceiling = CHARS_PER_BLOCK * MAX_BLOCKS_PER_CAPTION
    assert shipped <= ceiling, (
        f"CAPTION_SENTENCE_MAX_CHARS is {shipped}, more than the "
        f"{MAX_BLOCKS_PER_CAPTION} displayed blocks a caption may span "
        f"({ceiling} chars at {CHARS_PER_BLOCK} per block). A caption that "
        f"long is still on screen after the next two have been spoken."
    )


def test_the_ceiling_still_admits_the_longest_caption_actually_observed():
    """A ceiling that splits ordinary sentences would cost translation quality.

    The longest caption measured across a live katha session was 141
    characters, so the ceiling must sit above that — otherwise it starts
    cutting whole sentences into fragments, and a Gujarati fragment cut before
    its verb is precisely what SentenceAssembler exists to prevent.
    """
    import live_captions

    shipped = live_captions.SENTENCE_MAX_CHARS_DEFAULT
    assert shipped > 141
