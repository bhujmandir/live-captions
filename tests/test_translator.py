"""Choosing, prompting and reading a translator.

The accuracy problem this addresses is not that Mayura is bad at Gujarati —
it is that Mayura cannot be *told* anything. It has no prompt, so it cannot
be told that this is a Hindu devotional discourse, that કથા is a recital of
scripture rather than a story, or what the swami's name is. A general model
can be told all three, which is the whole reason for a second backend.

The HTTP call is not tested here. What is tested is everything around it:
what we ask for, what we do with the answer, and — most important for a live
hall — what happens when the answer does not come.
"""
import pytest

import live_captions as lc


# ── the request ──────────────────────────────────────────────────────────

def test_the_brief_and_glossary_reach_the_model():
    """Without these the second backend is just a slower Mayura."""
    req = lc.build_gemini_request(
        "ભગવાનની કથાનો આરંભ કરીએ છીએ.",
        source_lang="gu-IN", target_lang="en-IN",
        brief="A Hindu devotional katha at a Swaminarayan mandir.",
        glossary={"કથા": "discourse"},
        context=[],
    )
    blob = repr(req)
    assert "Swaminarayan" in blob
    assert "કથા" in blob and "discourse" in blob
    assert "ભગવાનની કથાનો આરંભ કરીએ છીએ." in blob


def test_recent_lines_are_offered_as_context_not_as_input():
    """The model must translate the new sentence only. Prior lines are there
    so pronouns and topic stay consistent across captions — if they leaked
    into the output the same line would be shown twice."""
    req = lc.build_gemini_request(
        "તેમણે કહ્યું.", source_lang="gu-IN", target_lang="en-IN",
        brief="", glossary={}, context=["The swami sat down."],
    )
    parts = req["contents"][-1]["parts"][0]["text"]
    assert parts.strip().endswith("તેમણે કહ્યું.")


def test_thinking_is_switched_off_because_this_is_live():
    """A reasoning pass costs seconds. A caption that is right but arrives
    after the speaker has moved on is not right."""
    req = lc.build_gemini_request(
        "કંઈક.", source_lang="gu-IN", target_lang="en-IN",
        brief="", glossary={}, context=[],
    )
    assert req["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
    assert req["generationConfig"]["temperature"] == 0


# ── the response ─────────────────────────────────────────────────────────

def test_a_normal_answer_is_read_out():
    data = {"candidates": [{"content": {"parts": [{"text": "We begin the katha."}]}}]}
    assert lc.parse_gemini_response(data) == "We begin the katha."


def test_surrounding_quotes_and_whitespace_are_stripped():
    """Models like to wrap a translation in quotes. On a caption bar those
    are noise."""
    data = {"candidates": [{"content": {"parts": [{"text": '  "We begin."\n'}]}}]}
    assert lc.parse_gemini_response(data) == "We begin."


@pytest.mark.parametrize("data", [
    {},
    {"candidates": []},
    {"candidates": [{"content": {"parts": []}}]},
    {"candidates": [{"content": {"parts": [{"text": "   "}]}}]},
    {"candidates": [{"finishReason": "SAFETY"}]},
    {"promptFeedback": {"blockReason": "OTHER"}},
])
def test_anything_unusable_reads_as_no_answer(data):
    """None means 'fall back', and every shape that is not a translation has
    to produce it — including a safety block, which on devotional content is
    a real possibility and must not put the word 'None' on the hall screen."""
    assert lc.parse_gemini_response(data) is None


def test_a_multi_part_answer_is_joined():
    data = {"candidates": [{"content": {"parts": [{"text": "We begin "},
                                                  {"text": "the katha."}]}}]}
    assert lc.parse_gemini_response(data) == "We begin the katha."


# ── the day's passage ────────────────────────────────────────────────────
#
# A real Vachanamrut reading mangled the passage's central term five
# different ways: શાપિત બુદ્ધિ ("cursed intellect") was heard as શાંતિ and
# શાર્પ and rendered "He's crazy", "He's brainwashed", "He's real smooth",
# "He becomes a victim", "Acurse". No glossary entry repairs a mishearing —
# but naming the passage makes the right words the expected ones.

def test_the_days_passage_is_offered_as_reference(tmp_path):
    f = tmp_path / "g.json"
    f.write_text('{"brief": "A katha.", "reference": "That person\'s '
                 'intellect is cursed.", "terms": {}}', encoding="utf-8")
    brief, _ = lc.load_glossary(str(f))
    assert "A katha." in brief
    assert "intellect is cursed" in brief


def test_the_reference_is_framed_as_reference_not_as_a_script(tmp_path):
    """The speaker expounds far more than he reads. If the model treated the
    passage as the thing to output, it would caption words nobody said — so
    the instruction not to copy from it has to travel with it."""
    f = tmp_path / "g.json"
    f.write_text('{"reference": "That person\'s intellect is cursed."}',
                 encoding="utf-8")
    brief, _ = lc.load_glossary(str(f))
    assert "Do NOT copy from it" in brief
    assert "translate what was actually said" in brief


def test_a_missing_glossary_is_not_an_error():
    assert lc.load_glossary("/nonexistent/glossary.json") == ("", {})


# ── surviving this material, and this API ────────────────────────────────
#
# Three things found by actually calling the API rather than reading about
# it: the shipped default model returns 404 ("no longer available to new
# users"), `thinkingConfig` is accepted by gemini-3.1-flash-lite and rejected
# with a 400 by gemini-3.5-flash-lite, and nothing in the request told the
# service that a passage about curses, harm and failing one's parents is
# devotional scripture rather than abuse.

def test_devotional_content_about_curses_is_not_left_to_default_safety():
    """A Vachanamrut passage is *about* being cursed for hurting a sant or
    failing one's parents. Blocked, it looks exactly like a timeout and
    silently demotes to the weaker translator."""
    req = lc.build_gemini_request("શાપ.", source_lang="gu-IN",
                                  target_lang="en-IN", brief="", glossary={},
                                  context=[])
    cats = {s["category"] for s in req["safetySettings"]}
    assert "HARM_CATEGORY_DANGEROUS_CONTENT" in cats
    assert "HARM_CATEGORY_HARASSMENT" in cats
    assert all(s["threshold"] == "BLOCK_NONE" for s in req["safetySettings"])


def test_output_is_bounded_so_a_runaway_answer_cannot_hold_the_caption_bar():
    req = lc.build_gemini_request("કંઈક.", source_lang="gu-IN",
                                  target_lang="en-IN", brief="", glossary={},
                                  context=[])
    assert 0 < req["generationConfig"]["maxOutputTokens"] <= 512


def test_a_model_that_rejects_the_thinking_field_still_gets_a_request():
    """gemini-3.5-flash-lite 400s on the identical field gemini-3.1-flash-lite
    accepts, so it cannot be sent unconditionally."""
    with_it = lc.build_gemini_request("ક.", source_lang="gu-IN", target_lang="en-IN",
                                      brief="", glossary={}, context=[])
    assert "thinkingConfig" in with_it["generationConfig"]
    without = lc.build_gemini_request("ક.", source_lang="gu-IN", target_lang="en-IN",
                                      brief="", glossary={}, context=[],
                                      allow_thinking_config=False)
    assert "thinkingConfig" not in without["generationConfig"]
    assert without["contents"] == with_it["contents"]


# ── telling one silence from another ─────────────────────────────────────

def test_a_safety_block_is_named_rather_than_looking_like_a_timeout():
    assert lc.describe_gemini_refusal(
        {"promptFeedback": {"blockReason": "OTHER"}}) == "blocked: OTHER"
    assert lc.describe_gemini_refusal(
        {"candidates": [{"finishReason": "SAFETY"}]}) == "blocked: SAFETY"


def test_a_truncated_answer_is_named_too():
    assert lc.describe_gemini_refusal(
        {"candidates": [{"finishReason": "MAX_TOKENS"}]}) == "truncated"


def test_an_ordinary_empty_answer_is_not_dressed_up_as_a_block():
    assert lc.describe_gemini_refusal({"candidates": []}) == "no candidates"
    assert lc.describe_gemini_refusal({}) == "no candidates"


# ── the switch has to actually do something ──────────────────────────────
#
# Shipped broken: on the default gu→en direction Saaras already returns
# English, so the fan-out had nothing left to translate and the chosen
# translator was never called. CAPTION_TRANSLATOR=gemini was a no-op unless
# an unrelated recording flag happened to be on, and docs/how-it-works.md said it simply
# worked. An external translator needs the SOURCE text, so choosing one has
# to put the pipeline on the two-hop path.

def _wanted_after_fanout(pipe, target):
    """What the emitter is left with once Saaras's own output is discounted."""
    wanted = {target}
    wanted.discard(pipe["saaras_output_lang"])
    return wanted


def test_choosing_an_external_translator_puts_it_in_the_path(monkeypatch):
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    monkeypatch.setattr(lc, "RECORD_SOURCE_TEXT", False)
    pipe = lc.derive_pipeline("gu-IN", "en-IN")
    assert pipe["sarvam_mode"] == "transcribe"
    assert pipe["saaras_output_lang"] == "gu-IN"
    assert pipe["needs_translation"] is True
    assert _wanted_after_fanout(pipe, "en-IN") == {"en-IN"}


def test_sarvam_keeps_the_fast_one_call_path(monkeypatch):
    """Not a regression: the one-call path is why the default is quick."""
    monkeypatch.setattr(lc, "TRANSLATOR", "sarvam")
    monkeypatch.setattr(lc, "RECORD_SOURCE_TEXT", False)
    pipe = lc.derive_pipeline("gu-IN", "en-IN")
    assert pipe["sarvam_mode"] == "translate"
    assert pipe["needs_translation"] is False


def test_recording_the_source_still_forces_two_hops_on_its_own(monkeypatch):
    monkeypatch.setattr(lc, "TRANSLATOR", "sarvam")
    monkeypatch.setattr(lc, "RECORD_SOURCE_TEXT", True)
    assert lc.derive_pipeline("gu-IN", "en-IN")["sarvam_mode"] == "transcribe"


def test_english_in_english_out_never_translates(monkeypatch):
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    pipe = lc.derive_pipeline("en-IN", "en-IN")
    assert pipe["needs_translation"] is False


# ── the fallback ladder ──────────────────────────────────────────────────
#
# `translate_line` is the single door for the live path, and its contract is
# the one that keeps the hall from staring at a blank bar: three rungs, and
# no rung may return nothing. That was prose until now — the function had no
# tests at all, on either axis of the review.

import pytest


class FakeHTTP:
    """Stands in for the aiohttp session. Nothing here touches a network."""
    def __init__(self):
        self.calls = []


@pytest.fixture
def ladder(monkeypatch):
    """Drive both rungs by hand and record which was used."""
    calls = []

    async def fake_gemini(session, api_key, text, **kw):
        calls.append(("gemini", text))
        return fake_gemini.result

    async def fake_mayura(session, api_key, text, **kw):
        calls.append(("mayura", text))
        return fake_mayura.result

    fake_gemini.result = "from gemini"
    fake_mayura.result = "from mayura"
    monkeypatch.setattr(lc, "_gemini_translate", fake_gemini)
    monkeypatch.setattr(lc, "_mayura_translate", fake_mayura)
    return calls, fake_gemini, fake_mayura


async def test_the_preferred_translator_is_used_when_it_answers(ladder, monkeypatch):
    calls, _, _ = ladder
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="k")
    assert out == "from gemini"
    assert [c[0] for c in calls] == ["gemini"]


@pytest.mark.parametrize("failure", [None, ""])
async def test_a_silent_translator_falls_through_to_mayura(ladder, monkeypatch, failure):
    """The rung that matters. A timeout, a safety block, an HTTP error and an
    empty candidate all arrive here as the same nothing, and every one of
    them must still put words on the screen."""
    calls, g, _ = ladder
    g.result = failure
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="k")
    assert out == "from mayura"
    assert [c[0] for c in calls] == ["gemini", "mayura"]


async def test_no_key_means_the_preferred_translator_is_not_even_tried(
        ladder, monkeypatch):
    calls, _, _ = ladder
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="")
    assert out == "from mayura"
    assert [c[0] for c in calls] == ["mayura"]


async def test_sarvam_never_reaches_for_the_other_translator(ladder, monkeypatch):
    calls, _, _ = ladder
    monkeypatch.setattr(lc, "TRANSLATOR", "sarvam")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="k")
    assert out == "from mayura"
    assert [c[0] for c in calls] == ["mayura"]


async def test_the_bottom_rung_still_returns_words(ladder, monkeypatch):
    """Mayura's own contract is to hand back the source text rather than
    nothing. Whatever it returns is what the hall sees, and it is never
    empty — a blank caption bar in a full hall has no recovery."""
    calls, g, m = ladder
    g.result = None
    m.result = "ક"          # Mayura failed and handed back the source
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="k")
    assert out == "ક"


# ── The door's own promise ───────────────────────────────────────────────
#
# translate_line's docstring says "never returns nothing". Until 2026-09-01
# that promise rested entirely on _mayura_translate remembering to hand back
# the source text in each of its three failure paths — and every test that
# looked like it covered the ladder patched _mayura_translate out, so all of
# them were asserting pass-through against a stub.
#
# The proof: changing the real _mayura_translate's empty-response branch to
# return "" instead of the source text left the whole suite green (101 passed).
# These tests fail on that change. They are the reason the door now closes its
# own promise rather than trusting the rung below it.

@pytest.mark.parametrize("nothing", [None, "", "   "])
async def test_a_caption_is_never_empty_even_if_every_rung_returns_nothing(
        ladder, monkeypatch, nothing):
    """The bottom of the ladder is the source text, and it is the DOOR's job.

    A blank caption bar in a full hall is the one outcome with no recovery,
    so this holds even when both rungs return nothing at all."""
    calls, g, m = ladder
    g.result = nothing
    m.result = nothing
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", gemini_key="k")
    assert out == "ક", "the source text must reach the screen when nothing else can"
    assert [c[0] for c in calls] == ["gemini", "mayura"], "both rungs were tried first"


async def test_a_caption_is_never_empty_when_the_bottom_rung_raises(ladder, monkeypatch):
    """_mayura_translate catches broadly today, but the door must not depend
    on that staying true — the invariant belongs to translate_line."""
    calls, g, m = ladder
    g.result = None

    async def boom(session, api_key, text, **kw):
        calls.append(("mayura", text))
        raise RuntimeError("Mayura exploded")

    monkeypatch.setattr(lc, "_mayura_translate", boom)
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    with pytest.raises(RuntimeError):
        await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                target_lang="en-IN", gemini_key="k")
    # Documents today's behaviour deliberately: the raise is NOT swallowed
    # here, it is handled at the fan-out, which substitutes the source text
    # per target. Swallowing it here would hide a broken rung forever.


async def test_the_sarvam_only_path_also_never_returns_nothing(ladder, monkeypatch):
    calls, _, m = ladder
    m.result = ""
    monkeypatch.setattr(lc, "TRANSLATOR", "sarvam")
    out = await lc.translate_line(FakeHTTP(), "ક", source_lang="gu-IN",
                                  target_lang="en-IN", sarvam_key="k")
    assert out == "ક"
    assert [c[0] for c in calls] == ["mayura"], "Gemini is not consulted on the sarvam path"


# ── The typo trap ────────────────────────────────────────────────────────
#
# CAPTION_TRANSLATOR=gemeni used to buy the expensive two-hop pipeline, deliver
# Mayura's captions, and print the line the mandir PC uses to verify that
# Gemini is live. Three readers of one raw string, three different answers.

def test_an_unrecognised_translator_lands_on_a_coherent_state():
    """Whatever the value, every reader must agree about it."""
    assert lc.TRANSLATOR in lc.TRANSLATORS, (
        "the module-level value is validated at read, so it is always one of "
        "the translators that actually exist"
    )


# ── The bottom rung itself, not a stub of it ─────────────────────────────
#
# The review found that no test anywhere exercised the REAL _mayura_translate:
# every ladder test patches it out, and its only other caller is a benchmark
# script. That is precisely why a one-line mutation to its empty-response
# branch left the whole suite green. These drive the actual function.

class _FakeResponse:
    def __init__(self, status=200, payload=None, body=""):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._body = body

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self._payload
    async def text(self): return self._body


class _FakeSession:
    """Minimal stand-in for aiohttp. Never touches a network."""
    def __init__(self, response): self._response = response
    def post(self, *a, **kw): return self._response


async def test_the_real_bottom_rung_returns_the_source_on_an_http_error():
    session = _FakeSession(_FakeResponse(status=500, body="upstream on fire"))
    out = await lc._mayura_translate(session, "key", "ભગવાન",
                                     source_lang="gu-IN", target_lang="en-IN")
    assert out == "ભગવાન"


@pytest.mark.parametrize("payload", [
    {},                                  # field absent entirely
    {"translated_text": ""},             # present and empty
    {"translated_text": "   "},          # present and blank — truthy, useless
    {"translated_text": None},           # present and null
])
async def test_the_real_bottom_rung_returns_the_source_on_an_empty_answer(payload):
    """This is the exact branch whose mutation the old suite could not see."""
    session = _FakeSession(_FakeResponse(status=200, payload=payload))
    out = await lc._mayura_translate(session, "key", "ભગવાન",
                                     source_lang="gu-IN", target_lang="en-IN")
    assert out == "ભગવાન", "an empty answer must degrade to the source, never to nothing"


async def test_the_real_bottom_rung_returns_the_source_when_the_call_raises():
    class Exploding:
        def post(self, *a, **kw): raise RuntimeError("connection reset")
    out = await lc._mayura_translate(Exploding(), "key", "ભગવાન",
                                     source_lang="gu-IN", target_lang="en-IN")
    assert out == "ભગવાન"


async def test_the_real_bottom_rung_passes_the_translation_through_when_it_works():
    session = _FakeSession(_FakeResponse(payload={"translated_text": " Bhagvan "}))
    out = await lc._mayura_translate(session, "key", "ભગવાન",
                                     source_lang="gu-IN", target_lang="en-IN")
    assert out == "Bhagvan", "trimmed, but otherwise exactly what the service said"


# ── "Configured" and "actually in the path" are different questions ──────
#
# CAPTION_TRANSLATOR=gemini with an empty GEMINI_API_KEY means Gemini is never
# attempted — and because it is never attempted, the per-line "falling back to
# Mayura" never prints either. A log naming gemini over Mayura's captions with
# zero fallbacks recorded is precisely the bug this change set removes, so the
# label must answer the reachable question, not the configured one.

def test_the_reachable_translator_is_gemini_only_when_a_key_exists(monkeypatch):
    monkeypatch.setattr(lc, "TRANSLATOR", "gemini")
    monkeypatch.setattr(lc, "GEMINI_API_KEY", "")
    assert lc.effective_translator("") == "sarvam", \
        "configured for gemini, but with no key it is Mayura that answers"
    assert lc.effective_translator("a-key") == "gemini"


def test_the_reachable_translator_is_sarvam_when_sarvam_is_chosen(monkeypatch):
    monkeypatch.setattr(lc, "TRANSLATOR", "sarvam")
    monkeypatch.setattr(lc, "GEMINI_API_KEY", "a-key")
    assert lc.effective_translator("a-key") == "sarvam", \
        "a key lying around does not put Gemini in the path"
