"""Shared fixtures.

Everything here exists to make `sarvam_loop` runnable on a laptop with no
network, no `SARVAM_API_KEY` and no audio device.
"""

from __future__ import annotations

import time as real_time

import pytest

import live_captions

from .fakes import FakeBroadcaster, VirtualClock


class _TimeShim:
    """`time` with a clock the test controls.

    `live_captions` uses `time.time()` for every timing decision in the audio
    path — the silence gate, the level-meter cadence, the throughput log. The
    shim swaps just that one function for the virtual clock and passes
    everything else (`strftime`, `monotonic`, …) through to the real module.

    It does *not* reach the reconnect backoff, which waits on the event loop's
    own clock. See the note in docs/how-it-works.md's Tests section.
    """

    def __init__(self, clock: VirtualClock):
        self._clock = clock

    def time(self) -> float:
        return self._clock.now

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def clock(monkeypatch) -> VirtualClock:
    """A virtual clock, installed over the module's `time`."""
    c = VirtualClock()
    monkeypatch.setattr(live_captions, "time", _TimeShim(c))
    return c


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Prove the suite needs no credentials by removing any that exist.

    A real key in the developer's environment (or in `.env`, which the module
    loads on import) must not be what makes these tests pass.
    """
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def documented_defaults(monkeypatch):
    """Pin every knob the module reads from the environment at import.

    `live_captions` loads `.env` on import, so a machine that has tuned a
    knob runs a different program than the one these tests describe. That is
    not hypothetical: the mandir PC keeps `SARVAM_RECORD_SOURCE=on` in `.env`
    to record the source Gujarati, which flips the pipeline to two-hop and
    turned `test_speech_is_captioned_and_silence_is_gated` red on the one
    machine where the feature is actually switched on — and red at the worst
    possible moment, since anyone running the suite at the mandir before
    katha then has to judge under time pressure whether the failure is real.

    `test_connection_state.py` already guards one instance of this with its
    `floor` fixture. This is the same guard applied to all of them, so the
    next env-derived constant is covered the day it is added rather than the
    day it breaks someone.

    A test that is *about* one of these knobs overrides it explicitly.
    """
    for name, value in [
        ("RECORD_SOURCE_TEXT",    False),
        ("HEALTHY_SESSION_SEC",   30.0),
        ("RECONNECT_MIN_INTERVAL_SEC", 1.0),
        ("SENTENCE_MODE",         True),
        ("SENTENCE_MAX_WAIT_SEC", 8.0),
        ("SENTENCE_QUIET_SEC",    2.5),
        ("SENTENCE_MAX_CHARS",    180),
        ("SENTENCE_MIN_WORDS",    5),
        # NOT the shipped default, which is "gemini". Pinned here because the
        # suite must never reach a network, and an unset key would make the
        # behaviour depend on the developer's .env. The shipped default's
        # own shape is asserted directly in test_translator.py
        # (`test_choosing_an_external_translator_puts_it_in_the_path`), and
        # every rung of the fallback ladder is covered there too — so what
        # this pin costs is covered elsewhere rather than left untested.
        ("TRANSLATOR",            "sarvam"),
        ("GEMINI_API_KEY",        ""),
    ]:
        assert hasattr(live_captions, name), f"{name} no longer exists — update this fixture"
        monkeypatch.setattr(live_captions, name, value)


@pytest.fixture
def state() -> dict:
    """The session state dict `sarvam_loop` reads its direction from.

    An Indic source into English is the one-call path: the speech service
    translates as it transcribes, so no separate text-translation step — and
    therefore no HTTP — is involved.
    """
    return {"source": "gu-IN", "target": "en-IN"}
