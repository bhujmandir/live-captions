"""The substitution rules — the last thing that touches a caption before the
hall reads it.

`apply_rules` is a pure function on the output path of every single caption,
its longest-pattern-first ordering is subtle enough to carry a comment
explaining it, and until now nothing exercised it. Every existing test runs it
with an empty rules list, so the CALL SITE was covered and the BEHAVIOUR was
not.

These test external behaviour only: text and rules in, text and fired ids out.
"""
import json

import live_captions as lc


def rule(id, pattern, replacement, *, regex=False, enabled=True):
    r = lc.Rule(id=id, pattern=pattern, replacement=replacement,
                regex=regex, enabled=enabled)
    r.compile_()
    return r


# ── The ordering that carries the comment ────────────────────────────────

def test_a_longer_phrase_wins_over_its_own_substring():
    """The documented reason this sort exists. Without it, "stories" fires
    first, and "religious stories" can never match its own text again."""
    rules = [rule("short", "stories", "kathas"),
             rule("long", "religious stories", "kathas")]
    out, fired = lc.apply_rules("He tells religious stories", rules)
    assert out == "He tells kathas"
    assert fired == ["long"], "the long pattern consumed the text first"


def test_ordering_does_not_depend_on_the_order_rules_were_given():
    rules_a = [rule("short", "stories", "X"), rule("long", "religious stories", "Y")]
    rules_b = [rule("long", "religious stories", "Y"), rule("short", "stories", "X")]
    assert lc.apply_rules("religious stories", rules_a)[0] == \
           lc.apply_rules("religious stories", rules_b)[0]


def test_equal_length_patterns_are_applied_deterministically():
    """Same length, so the tie-break is the rule id — the same input must not
    produce different captions on different runs."""
    rules = [rule("bbb", "abcd", "2"), rule("aaa", "wxyz", "1")]
    first = lc.apply_rules("abcd wxyz", rules)
    for _ in range(5):
        assert lc.apply_rules("abcd wxyz", rules) == first


# ── What "fired" means ───────────────────────────────────────────────────

def test_a_rule_that_matches_nothing_does_not_report_itself_as_fired():
    rules = [rule("r1", "Bhagwan", "Bhagvan")]
    out, fired = lc.apply_rules("Jay Swaminarayan", rules)
    assert out == "Jay Swaminarayan"
    assert fired == []


def test_only_the_rules_that_actually_changed_the_text_are_reported():
    rules = [rule("hit", "story", "katha"), rule("miss", "temple", "mandir")]
    out, fired = lc.apply_rules("He told a story", rules)
    assert out == "He told a katha"
    assert fired == ["hit"]


# ── Whole-word literal matching ──────────────────────────────────────────

def test_a_literal_rule_matches_whole_words_only():
    """Substring matching here would rewrite the inside of other words —
    on a temple screen, silently."""
    rules = [rule("r", "sant", "sant")]
    out, fired = lc.apply_rules("The pheasant flew", rules)
    assert out == "The pheasant flew", "'sant' inside 'pheasant' must not match"
    assert fired == []


def test_a_literal_rule_is_case_insensitive():
    rules = [rule("r", "bhagwan", "Bhagvan")]
    out, _ = lc.apply_rules("BHAGWAN and Bhagwan", rules)
    assert out == "Bhagvan and Bhagvan"


def test_regex_metacharacters_in_a_literal_rule_are_taken_literally():
    """A literal rule containing '.' or '(' must not behave as a regex."""
    rules = [rule("r", "a.c", "REPLACED")]
    out, fired = lc.apply_rules("abc", rules)
    assert out == "abc", "'a.c' must not match 'abc' when regex is off"
    assert fired == []


# ── Regex rules ──────────────────────────────────────────────────────────

def test_a_regex_rule_matches_as_a_pattern():
    rules = [rule("r", r"swami\w*", "Swami", regex=True)]
    out, fired = lc.apply_rules("swamiji spoke", rules)
    assert out == "Swami spoke"
    assert fired == ["r"]


def test_a_rule_with_a_broken_pattern_is_skipped_rather_than_crashing():
    """A bad regex typed into the operator UI must never take the captions
    down mid-katha — it is skipped and the error surfaced instead."""
    bad = rule("bad", "(unclosed", "X", regex=True)
    good = rule("good", "story", "katha")

    out, fired = lc.apply_rules("He told a story", [bad, good])

    # External behaviour only. This used to also assert `bad._compiled is None
    # and bad._error`, which is private state and says nothing the two lines
    # below do not already prove: the broken rule changed nothing, the good one
    # still ran, and the captions stayed up.
    assert out == "He told a katha"
    assert fired == ["good"]


# ── Disabled rules and the empty cases ───────────────────────────────────

def test_a_disabled_rule_does_nothing():
    rules = [rule("off", "story", "katha", enabled=False)]
    out, fired = lc.apply_rules("He told a story", rules)
    assert out == "He told a story"
    assert fired == []


def test_empty_text_and_empty_rules_are_returned_untouched():
    assert lc.apply_rules("", [rule("r", "a", "b")]) == ("", [])
    assert lc.apply_rules("some caption", []) == ("some caption", [])


def test_rules_compose_in_sequence():
    rules = [rule("a", "story", "katha"), rule("b", "temple", "mandir")]
    out, fired = lc.apply_rules("a story at the temple", rules)
    assert out == "a katha at the mandir"
    assert sorted(fired) == ["a", "b"]


# ── The rule pass must not undo the ladder's promise ─────────────────────
#
# translate_line guarantees a non-empty caption. The rule pass runs AFTER it
# and could hand back nothing: an empty `replacement` is accepted by the API
# and by rules.json, so one bad rule blanked the caption bar in a full hall
# with nothing logged anywhere. apply_rules_safely is where that stops.

def test_a_rule_that_erases_the_whole_caption_is_refused():
    """The bug: (".*" -> "") wipes the line and broadcasts an empty string."""
    wipe = rule("wipe", ".*", "", regex=True)
    out, fired = lc.apply_rules_safely("Maharaj wrote the Shikshapatri", [wipe])
    assert out == "Maharaj wrote the Shikshapatri", "the un-corrected text still reaches the hall"
    assert fired == [], "a refused pass reports nothing as fired"


def test_a_rule_that_erases_the_only_word_is_refused():
    kill = rule("kill", "Maharaj", "")
    out, fired = lc.apply_rules_safely("Maharaj", [kill])
    assert out == "Maharaj"
    assert fired == []


def test_deleting_a_word_from_a_longer_caption_still_works():
    """An empty replacement is a legitimate way to drop a filler word. Only
    emptying the ENTIRE caption is refused."""
    drop = rule("drop", "erm", "")
    out, fired = lc.apply_rules_safely("erm Maharaj spoke", [drop])
    assert out.strip() == "Maharaj spoke"
    assert fired == ["drop"]


def test_the_exclusion_marker_is_not_treated_as_blank():
    """Suppressing an utterance deliberately is spelled '…', which is not
    empty and must pass through."""
    excl = rule("x", "Maharaj", "…")
    out, fired = lc.apply_rules_safely("Maharaj", [excl])
    assert out == "…"
    assert fired == ["x"]


def test_whitespace_only_output_counts_as_blank():
    """A caption of spaces is truthy in Python and blank on a screen."""
    spaces = rule("sp", "Maharaj", "   ")
    out, fired = lc.apply_rules_safely("Maharaj", [spaces])
    assert out == "Maharaj"
    assert fired == []


def test_an_already_empty_caption_is_passed_through_untouched():
    """The guard protects a caption that HAD words. It does not invent any."""
    out, fired = lc.apply_rules_safely("", [rule("r", "a", "b")])
    assert out == ""
    assert fired == []


def test_a_normal_correction_is_unaffected():
    out, fired = lc.apply_rules_safely("He told a story", [rule("r", "story", "katha")])
    assert out == "He told a katha"
    assert fired == ["r"]


# ── The overlay status board ─────────────────────────────────────────────
#
# 🔴 Exists because the overlay runs inside vMix's browser input on an
# unattended machine in Bolton where nobody can open devtools. The DOM
# attribute it publishes is readable from a browser and useless from a
# terminal, so "what is the hall screen actually showing?" had no answer from
# outside. Asked for by the mandir PC after a deploy it could not report on.
#
# The handlers are called directly rather than through an HTTP client. That
# keeps the suite's promise of no network and no extra dependency, and these
# two handlers are pure request-in / response-out with no middleware to miss.


class _FakeRequest:
    """Just enough request for these two handlers: a JSON body, or a failure."""

    def __init__(self, payload=None, raises=False):
        self._payload = payload
        self._raises = raises

    async def json(self):
        if self._raises:
            raise ValueError("not json at all")
        return self._payload


def _body(resp):
    return json.loads(resp.body.decode())


async def test_a_surface_can_publish_what_it_is_showing():
    lc._overlay_reports.clear()

    await lc.handle_overlay_report(_FakeRequest({
        "surface":  "overlay-abc",
        "onScreen": "Jay Swaminarayan.",
        "state":    {"budget": 102, "pending": 1, "dropped": 0},
    }))

    board = _body(await lc.handle_overlay_status(_FakeRequest()))

    assert len(board["surfaces"]) == 1
    surface = board["surfaces"][0]
    assert surface["surface"] == "overlay-abc"
    assert surface["on_screen"] == "Jay Swaminarayan."
    assert surface["budget"] == 102
    # The age is the point: a surface that stopped reporting reads exactly like
    # a healthy one unless the age is in front of you.
    assert "age_sec" in surface


async def test_a_surface_reporting_again_replaces_its_previous_report():
    lc._overlay_reports.clear()

    for text in ("first", "second", "third"):
        await lc.handle_overlay_report(_FakeRequest({"surface": "s1", "onScreen": text}))

    board = _body(await lc.handle_overlay_status(_FakeRequest()))

    # A status board, not a log.
    assert len(board["surfaces"]) == 1
    assert board["surfaces"][0]["on_screen"] == "third"


async def test_reloading_pages_cannot_grow_the_board_without_bound():
    """Every reload reports under a fresh id. A katha-length run of them must
    not accumulate."""
    lc._overlay_reports.clear()

    for i in range(60):
        await lc.handle_overlay_report(_FakeRequest({"surface": f"s{i}", "onScreen": "x"}))

    board = _body(await lc.handle_overlay_status(_FakeRequest()))

    assert len(board["surfaces"]) <= lc.MAX_OVERLAY_REPORTS


async def test_a_malformed_report_never_returns_an_error_to_the_hall_screen():
    """A diagnostics channel that can take a caption surface down is worse than
    no diagnostics channel. Every failure is swallowed into a 200."""
    lc._overlay_reports.clear()

    resp = await lc.handle_overlay_report(_FakeRequest(raises=True))

    assert resp.status == 200
    assert _body(resp)["ok"] is False
    assert lc._overlay_reports == {}


async def test_a_surface_that_stopped_reporting_is_marked_stale():
    """🔴 A dead surface still lists, and its `budget` and `opacity` read
    exactly like live values unless something says otherwise.

    Found by the mandir PC: it closed its test browser and the surface kept
    appearing, correctly aged — but someone reading the numbers off it would
    have been reading a ghost. `age_sec` alone requires the reader to know the
    threshold and do the arithmetic; the answer should be in front of them.
    """
    lc._overlay_reports.clear()

    await lc.handle_overlay_report(_FakeRequest({"surface": "ghost", "state": {"budget": 126}}))
    # Reach into the stored time rather than waiting it out — the same reason
    # the JS suite drives a virtual clock.
    lc._overlay_reports["ghost"]["at"] -= lc.OVERLAY_STALE_AFTER_SEC + 1

    board = _body(await lc.handle_overlay_status(_FakeRequest()))
    surface = board["surfaces"][0]

    assert surface["stale"] is True
    assert surface["budget"] == 126, "the last known values are kept — they are evidence"


async def test_a_surface_reporting_now_is_not_marked_stale():
    lc._overlay_reports.clear()

    await lc.handle_overlay_report(_FakeRequest({"surface": "live", "state": {"budget": 126}}))

    board = _body(await lc.handle_overlay_status(_FakeRequest()))

    assert board["surfaces"][0]["stale"] is False
