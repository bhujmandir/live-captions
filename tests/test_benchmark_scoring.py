"""Scoring a translation without pretending we can align it line by line.

Two axes, deliberately never merged into one number. A blend lets fluent
English that says the wrong thing beat clumsy English that says the right
thing, and that is the single failure this project cannot afford: a
confident wrong caption reads *more* fluently than a hesitant right one, and
nobody in the hall can tell.

**Terminology** is per-line and needs no alignment: where the Gujarati
contains a known term, does the English contain what the published
translation calls it?

**Similarity** is measured over the whole transcript, not per line. Aligning
138 machine-segmented fragments to a printed passage is a research problem
of its own, and a bad alignment would silently corrupt every per-line score.
"""
import pytest

from tools.benchmark_translators import (TermCheck, similarity,
                                         terminology_score)


TERMS = [
    TermCheck(source="શાપિત", expected=("cursed",)),
    TermCheck(source="કથા",   expected=("katha", "discourse")),
]


# ── terminology ──────────────────────────────────────────────────────────

def test_a_line_without_the_term_is_not_scored():
    """Only lines whose Gujarati actually contains the term can be judged;
    counting the rest would drown the signal in irrelevant passes."""
    s = terminology_score([("આજે આપણે ભેગા થયા.", "We gathered today.")], TERMS)
    assert s.applicable == 0
    assert s.hits == 0


def test_the_published_wording_scores():
    s = terminology_score([("એને બુદ્ધિ શાપિત.", "That person's intellect is cursed.")], TERMS)
    assert (s.applicable, s.hits) == (1, 1)


def test_a_fluent_wrong_answer_does_not_score():
    """'He's brainwashed' is perfectly readable English and completely wrong.
    This is the case the whole two-axis split exists for."""
    s = terminology_score([("એને બુદ્ધિ શાપિત.", "He's brainwashed.")], TERMS)
    assert (s.applicable, s.hits) == (1, 0)


def test_any_accepted_rendering_counts():
    for english in ("We begin the katha.", "We begin the discourse."):
        s = terminology_score([("કથાનો આરંભ.", english)], TERMS)
        assert s.hits == 1, english


def test_the_term_is_matched_case_insensitively():
    s = terminology_score([("શાપિત બુદ્ધિ", "CURSED INTELLECT")], TERMS)
    assert s.hits == 1


def test_a_missing_translation_scores_zero_rather_than_crashing():
    s = terminology_score([("એને બુદ્ધિ શાપિત.", None)], TERMS)
    assert (s.applicable, s.hits) == (1, 0)


def test_per_line_detail_is_kept_because_an_average_hides_the_finding():
    """One term wrong five different ways averages away to nothing."""
    s = terminology_score([
        ("શાપિત", "cursed"),
        ("શાપિત", "brainwashed"),
        ("શાપિત", "a victim"),
    ], TERMS)
    assert s.applicable == 3 and s.hits == 1
    assert [m.hit for m in s.misses_and_hits] == [True, False, False]


# ── similarity ───────────────────────────────────────────────────────────

def test_identical_text_is_one():
    assert similarity("the intellect is cursed", "the intellect is cursed") == 1.0


def test_unrelated_text_is_near_zero():
    assert similarity("the intellect is cursed", "prasad will be distributed") < 0.3


def test_wording_differences_score_between():
    v = similarity("That person's intellect is cursed",
                   "His intellect has been cursed")
    assert 0.3 < v < 1.0


def test_similarity_ignores_case_and_punctuation():
    assert similarity("Cursed intellect!", "cursed intellect") > 0.95


# ── a benchmark that cannot tell you it failed is worse than none ────────
#
# The first real run scored Gemini at 42% against Mayura's 58% and looked
# like a finding. It was not: 51 of 69 calls had been rejected for quota, and
# the harness had scored rate-limiting as bad translation. That is precisely
# the false conclusion this benchmark exists to prevent, so the harness has
# to refuse to headline a comparison it did not actually make.

from tools.benchmark_translators import comparable_subset, coverage_warning


def test_a_backend_that_mostly_did_not_answer_is_not_reported_as_worse():
    w = coverage_warning(answered=18, total=69, label="gemini")
    assert w is not None
    assert "18/69" in w


def test_full_coverage_needs_no_warning():
    assert coverage_warning(answered=69, total=69, label="gemini") is None


def test_a_couple_of_gaps_are_tolerated():
    """A translator that fell over once is still worth scoring."""
    assert coverage_warning(answered=68, total=69, label="x") is None


def test_the_comparison_falls_back_to_what_both_actually_answered():
    """Like for like, or not at all. Scoring one backend on lines the other
    never saw compares two different exams."""
    idx = comparable_subset({
        "a": ["one", "two", "three", None],
        "b": ["uno", None,  "tres",  None],
    })
    assert idx == [0, 2]


def test_an_empty_string_counts_as_no_answer():
    assert comparable_subset({"a": ["x", ""], "b": ["y", "z"]}) == [0]
