"""The previous lines the model is told about — proven on the LIVE path.

Gemini earns its place by being briefable: the domain brief, the glossary,
and the last few lines of the katha so a pronoun in this sentence still knows
who it refers to. The first two are visible in `glossary.json`; the third is
not visible anywhere. It is a list that lives inside one session, is filled in
one place, read in another, and shows up only inside a prompt string that goes
straight out over HTTPS.

That makes it exactly the kind of feature that can stop working and look
completely healthy: captions still arrive, latency does not move, no log line
changes. `test_translator.py` already asserts that `build_gemini_request` puts
a context list into the prompt when it is handed one — but nothing asserted
that the live session ever hands it one.

So these tests drive the real supervisor, with a fake speech service and a
fake HTTP session, and read the request body that would have gone to Gemini.
They fail if the wiring between the two ever comes apart.

⚠️ No network. The aiohttp session `_gemini_translate` posts on is replaced,
so a test that somehow reached the real API would raise rather than spend a
token.
"""
from __future__ import annotations

import asyncio

import aiohttp as real_aiohttp
import pytest

import live_captions as lc
from tests.fakes import (FakeSarvamClient, FakeSession, scripted_audio, speech)

# Three whole sentences: punctuated, and over `SENTENCE_MIN_WORDS`, so the
# assembler releases each one as it arrives rather than joining them.
SENTENCES = [
    "પહેલી પંક્તિ અહીં પૂરી થાય છે.",
    "બીજી પંક્તિ અહીં પૂરી થાય છે.",
    "ત્રીજી પંક્તિ અહીં પૂરી થાય છે.",
]

# What the fake model answers, one per call. Deliberately unlike anything the
# prompt already contains, so finding it in a LATER prompt can only mean the
# previous caption was carried forward.
def english(n: int) -> str:
    return f"the {['first', 'second', 'third', 'fourth'][n - 1]} english caption"


# Mirrors the wording in `build_gemini_request`. Used only for the cases where
# there is no previous caption to look for — everywhere else the assertion is
# on the caption text itself, which is the claim that actually matters.
CONTEXT_MARKER = "previous lines"


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._payload
    async def text(self): return ""


class RecordingHTTP:
    """Stands in for the pooled aiohttp session the translators post on.

    Records what was asked of Gemini, in order, and answers with a distinct
    caption each time. Mayura calls are recorded separately rather than
    refused: `_mayura_translate` swallows every exception, so a fake that
    raised there would fall back to the source text and look like a pass.
    """

    def __init__(self):
        self.gemini: list[dict] = []
        self.mayura: list[dict] = []
        self.closed = False

    def post(self, url, *, json=None, headers=None, timeout=None):
        if url.startswith(lc.SARVAM_TRANSLATE_URL):
            self.mayura.append(json)
            return _FakeResponse({"translated_text": "from mayura"})
        self.gemini.append(json)
        n = len(self.gemini)
        return _FakeResponse(
            {"candidates": [{"content": {"parts": [{"text": english(n)}]}}]})

    async def close(self):
        self.closed = True

    # ── views ────────────────────────────────────────────────────────────
    @property
    def prompts(self) -> list[str]:
        """The text of each request, as Gemini would have received it."""
        return [r["contents"][0]["parts"][0]["text"] for r in self.gemini]


class _AiohttpShim:
    """`aiohttp` with `ClientSession` replaced and everything else passed
    through — `_gemini_translate` builds a `ClientTimeout` from the same
    module. Same trick as conftest's clock shim, and for the same reason:
    swap the one thing under test, keep the module honest."""

    def __init__(self, session: RecordingHTTP):
        self._session = session

    def ClientSession(self, *a, **kw):
        return self._session

    def __getattr__(self, name):
        return getattr(real_aiohttp, name)


@pytest.fixture
def http(monkeypatch, tmp_path, documented_defaults, no_api_key) -> RecordingHTTP:
    """A session configured the way the mandir runs it: Gemini in the path.

    `documented_defaults` and `no_api_key` are requested by name so this
    fixture is guaranteed to run after them — they pin `TRANSLATOR` to
    "sarvam" and strip the key, which is right for every other test here and
    is precisely what these tests must undo.
    """
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    monkeypatch.setattr(lc, "GEMINI_API_KEY", "test-key-not-a-real-one")
    # The two-hop path needs a Sarvam key too, or the session disables the
    # translate step entirely and never reaches any backend.
    monkeypatch.setenv("SARVAM_API_KEY", "test-key-not-a-real-one")
    # The brief and glossary are the mandir's file, not this test's subject.
    # Point at a path that does not exist so the prompt is only what the code
    # puts there.
    monkeypatch.setattr(lc, "GLOSSARY_PATH", str(tmp_path / "no-glossary.json"))
    session = RecordingHTTP()
    monkeypatch.setattr(lc, "aiohttp", _AiohttpShim(session))
    return session


async def run_katha(clock, broadcaster, state, sentences: list[str]) -> None:
    """Drive the real supervisor through `sentences`, one per VAD final."""
    at_frame = {2 * (i + 1): text for i, text in enumerate(sentences)}

    async def talk(n, session):
        if n in at_frame:
            session.emit_transcript(at_frame[n])

    session = FakeSession(clock, on_frame=talk)
    client = FakeSarvamClient(sessions=[session])
    frames = [speech()] * (2 * len(sentences) + 4)

    await lc.sarvam_loop(
        scripted_audio(frames, clock, broadcaster), broadcaster, False,
        asyncio.Event(), state,
        sarvam_cfg={"model": "saaras:v3"}, client=client,
    )


# ── 1 · the previous line reaches the model ──────────────────────────────

async def test_the_previous_caption_is_offered_to_gemini_on_the_live_path(
        clock, broadcaster, state, http):
    """The whole point. Sentence two must be translated knowing sentence one.

    Fails if the context list is ever collected and then not passed, passed
    for a feed instead of the display, or emptied before the call.
    """
    await run_katha(clock, broadcaster, state, SENTENCES)

    assert len(http.prompts) == 3, (
        f"expected one Gemini call per sentence, got {len(http.prompts)}")
    assert http.mayura == [], "the fallback answered — Gemini was not in the path"

    assert english(1) in http.prompts[1], (
        "the second line was translated with no knowledge of the first:\n"
        + http.prompts[1])
    assert english(2) in http.prompts[2] and english(1) in http.prompts[2], (
        "the third line did not carry both preceding captions:\n"
        + http.prompts[2])


async def test_the_context_is_the_english_the_hall_saw_not_the_gujarati(
        clock, broadcaster, state, http):
    """Which language the previous lines are in is not a detail.

    The prompt names them "already translated". Feeding the Gujarati source
    under that label would make the context actively misleading — the model
    would be told the English it is continuing from is text it has not seen.
    """
    await run_katha(clock, broadcaster, state, SENTENCES)

    second = http.prompts[1]
    assert SENTENCES[1] in second, "the line to translate is missing entirely"
    assert SENTENCES[0] not in second, (
        "the previous line was offered as GUJARATI under a label that says "
        "'already translated':\n" + second)


# ── 2 · the first sentence of a session ──────────────────────────────────

async def test_the_first_sentence_has_no_context_and_is_still_captioned(
        clock, broadcaster, state, http):
    """An empty list must produce no context block at all — not an empty one.

    A heading with nothing under it invites the model to invent what it
    thinks is missing, and this is the sentence that opens the katha.
    """
    await run_katha(clock, broadcaster, state, SENTENCES[:1])

    first = http.prompts[0]
    assert CONTEXT_MARKER not in first, (
        "the opening sentence was given a previous-lines section with no "
        "previous lines in it:\n" + first)
    assert "english caption" not in first

    finals = [m["text"] for m in broadcaster.of_type("final")]
    assert finals == [english(1)], f"the opening caption never reached the hall: {finals}"


# ── 3 · the limit is a limit ─────────────────────────────────────────────

async def test_only_the_configured_number_of_lines_is_carried(
        clock, broadcaster, state, http, monkeypatch):
    """At 1, the third line knows the second and has forgotten the first."""
    monkeypatch.setattr(lc, "GEMINI_CONTEXT_LINES", 1)
    await run_katha(clock, broadcaster, state, SENTENCES)

    third = http.prompts[2]
    assert english(2) in third
    assert english(1) not in third, (
        "more lines were sent than GEMINI_CONTEXT_LINES allows — the prompt "
        "grows for the length of the katha:\n" + third)


async def test_zero_context_lines_means_no_context_at_all(
        clock, broadcaster, state, http, monkeypatch):
    """0 is the off switch, and it used to be the on switch.

    `context[-0:]` is the whole list and `del lines[:-0]` deletes nothing, so
    setting it to 0 offered every caption of the session so far and grew for
    as long as the katha ran. An operator turning the feature off to shed
    latency would have got the most expensive prompt in the program.
    """
    monkeypatch.setattr(lc, "GEMINI_CONTEXT_LINES", 0)
    await run_katha(clock, broadcaster, state, SENTENCES)

    for i, prompt in enumerate(http.prompts):
        assert CONTEXT_MARKER not in prompt, (
            f"line {i + 1} carried context with the feature switched off:\n"
            + prompt)
        assert "english caption" not in prompt


def test_a_zero_limit_is_honoured_by_the_request_builder(monkeypatch):
    """The same rule, asserted at the door — a caller that still holds a
    populated list (a session whose limit changed under it) must not have it
    sent."""
    monkeypatch.setattr(lc, "GEMINI_CONTEXT_LINES", 0)
    req = lc.build_gemini_request(
        "કંઈક.", source_lang="gu-IN", target_lang="en-IN",
        context=["a caption from earlier"])
    prompt = req["contents"][0]["parts"][0]["text"]
    assert "a caption from earlier" not in prompt
