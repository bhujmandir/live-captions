"""Score translators against the published English, offline.

Why this exists: the choice of translator was about to be made on an
impression. Two impressions, in fact, and both were wrong — the first
attributed a terminology failure to the translator when it was caused by
sentence fragmentation, and the second compared a briefed model against that
same handicapped baseline. Neither error would have survived a measurement.

**Two axes, never merged.** Blending them lets fluent English that says the
wrong thing beat clumsy English that says the right thing, and that is the
one failure mode a temple screen cannot afford: a confident wrong caption
reads *more* fluently than a hesitant right one.

**Terminology** is per-line and needs no alignment — where the Gujarati
contains a known term, does the English contain what the published
translation calls it?

**Similarity** is measured over the whole transcript. Aligning machine-
segmented fragments to a printed passage is a research problem in itself,
and a bad alignment would silently corrupt every per-line score. A
document-level figure is weaker, and it is honest.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TermCheck:
    """One term, and every English rendering we are willing to accept."""
    source: str                      # as it appears in the Gujarati
    expected: tuple[str, ...]        # any one of these counts as correct


@dataclass
class LineResult:
    source: str
    candidate: str | None
    term: str
    hit: bool


@dataclass
class TerminologyScore:
    applicable: int = 0              # lines whose Gujarati contained a term
    hits: int = 0                    # ...of which the English got it right
    misses_and_hits: list[LineResult] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.hits / self.applicable if self.applicable else 0.0


def terminology_score(pairs, terms) -> TerminologyScore:
    """`pairs` is [(gujarati, english_or_None), ...].

    Only lines whose Gujarati actually contains a term are scored. Counting
    the rest would drown the signal in irrelevant passes — most lines contain
    no term at all, so an all-lines average would sit near 100% however badly
    the terms were handled.
    """
    score = TerminologyScore()
    for source, candidate in pairs:
        for term in terms:
            if term.source not in (source or ""):
                continue
            got = (candidate or "").lower()
            hit = any(want.lower() in got for want in term.expected)
            score.applicable += 1
            score.hits += hit
            score.misses_and_hits.append(
                LineResult(source=source, candidate=candidate,
                           term=term.source, hit=hit))
    return score


def _normalise(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


def similarity(candidate: str, reference: str) -> float:
    """How close the wording is, 0..1. Case- and punctuation-insensitive.

    A blunt instrument on purpose. It cannot tell a good translation from a
    bad one — only how far the wording drifts from the published text — so it
    is reported beside the terminology axis and never instead of it.
    """
    return difflib.SequenceMatcher(
        None, _normalise(candidate), _normalise(reference)).ratio()


# ── the runner ───────────────────────────────────────────────────────────

def assemble(segments, *, quiet_sec, max_wait_sec, max_chars, min_words):
    """Replay captured VAD segments through the sentence assembler.

    This is what makes the comparison fair. The captured fragments are what
    the OLD pipeline fed the translator; feeding them to a candidate now
    would score it against input the tool no longer produces. Worse, it would
    flatter any briefed model — a two-word fragment is hard for everyone, so
    the handicapped baseline exaggerates the winner's margin. That mistake
    was made once already, on this very corpus.
    """
    import live_captions as lc
    a = lc.SentenceAssembler(quiet_sec=quiet_sec, max_wait_sec=max_wait_sec,
                             max_chars=max_chars, min_words=min_words,
                             enabled=True)
    out, prev = [], segments[0]["t"]
    for seg in segments:
        t = prev
        while t < seg["t"]:                      # the clock ticks between segments
            t = min(t + 0.1, seg["t"])
            due = a.due(now=t)
            if due:
                out.append(due)
        prev = seg["t"]
        ready = a.add(seg["gu"], now=seg["t"])
        if ready:
            out.append(ready)
    tail = a.flush()
    if tail:
        out.append(tail)
    return out


# ── refusing to report a comparison that was never made ──────────────────

# Below this share of lines answered, a backend's score says more about the
# API than about the translation.
MIN_COVERAGE = 0.95


def coverage_warning(*, answered: int, total: int, label: str) -> str | None:
    """A loud warning when a backend did not really take the test.

    The first real run of this harness scored Gemini at 42% against Mayura's
    58%, which read as a finding. It was not: 51 of 69 calls had been
    rejected for quota and the harness had scored rate-limiting as bad
    translation. A benchmark that cannot say "I did not measure this" is
    worse than no benchmark, because its number gets quoted.
    """
    if not total or answered / total >= MIN_COVERAGE:
        return None
    return (f"{label} answered only {answered}/{total} lines "
            f"({answered/total:.0%}). Its score below is NOT a quality "
            f"measurement — check the failure causes before quoting it.")


def comparable_subset(outputs_by_label: dict) -> list[int]:
    """Indices where every backend produced an answer.

    Scoring one backend on lines another never saw compares two different
    exams. When coverage is incomplete this is the only honest comparison
    left, and it is worth reporting beside the full-run numbers.
    """
    labels = list(outputs_by_label)
    if not labels:
        return []
    n = min(len(outputs_by_label[l]) for l in labels)
    return [i for i in range(n)
            if all(outputs_by_label[l][i] for l in labels)]
