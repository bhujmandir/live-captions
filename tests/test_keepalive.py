"""Tests for the keep-alive that holds the connection open through a silence.

The silence gate stops sending 1.5 s after the last loud audio, and the speech
service is understood to close a connection that has received nothing for
60 s. Any pause longer than about a minute would then end the session, and the
reconnect would land on the opening words of the next passage rather than
during the quiet. The fix is upstream of the reconnect: send a frame of
digital silence every so often so the connection never idles out at all.

The fake service here enforces that 60 s timeout, which is what stops "the
session survived the silence" being a claim that cannot fail. The test
immediately below the first one proves it: same script, keep-alive switched
off, session torn down.

⚠ The 60 s figure is the one the tool defends against, not one observed
live — see the note on `SERVICE_IDLE_TIMEOUT_SEC` in fakes.py. These tests
assert the connection is never left idle for long, which is worth holding
whatever the real number turns out to be.

No network, no `SARVAM_API_KEY`, no audio device. Run them with:

    uv run --extra dev pytest
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

import live_captions

from .fakes import (
    FRAME_SEC,
    SERVICE_IDLE_TIMEOUT_SEC,
    FakeSarvamClient,
    FakeSession,
    room,
    scripted_audio,
    speech,
)

# Long enough that no plausible keep-alive interval could span it, and the
# length of pause a long event actually contains — a musical item, a reading,
# a break.
SILENCE_SEC = 5 * 60

# What the tool is expected to ship with, stated here rather than read back
# from the module: a test that mirrors the implementation's own constant
# cannot tell you the default changed.
DEFAULT_KEEPALIVE_SEC = 20.0
MAX_KEEPALIVE_SEC = SERVICE_IDLE_TIMEOUT_SEC / 2

REPO_ROOT = Path(__file__).resolve().parent.parent


def script_with_silence(seconds: float = SILENCE_SEC) -> list[bytes]:
    """Speech, a long quiet room, speech again."""
    return [speech()] * 2 + [room()] * int(seconds / FRAME_SEC) + [speech()] * 2


async def run_supervisor(*, audio, broadcaster, state, client,
                         gate_cfg=None, timeout=60.0):
    """Run `sarvam_loop` to completion, exactly as `/api/start` does.

    `gate_cfg` is the same dict the start payload carries. A deployment
    configures the keep-alive through `.env` — see the env test at the bottom
    — but the payload is the per-session path and the one a test can drive
    without re-importing the module.
    """
    stop_event = asyncio.Event()
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            audio, broadcaster, False, stop_event, state,
            sarvam_cfg={"model": "saaras:v3"},
            gate_cfg=gate_cfg,
            client=client,
        ),
        timeout=timeout,
    )


async def test_a_five_minute_silence_never_tears_the_session_down(
        clock, broadcaster, state):
    """The ticket's whole point, on the shipped defaults.

    Five minutes of quiet between two passages, against a service that hangs
    up after 60 s of silence. Nothing is configured — this is what a first
    boot with no `.env` tuning does.
    """
    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[session])

    await run_supervisor(
        audio=scripted_audio(script_with_silence(), clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    # Not torn down, and not recovered from either — the connection opened
    # once and was still the same one when the speaker resumed.
    assert session.idle_closed is False
    assert client.connect_count == 1
    assert [m["attempt"] for m in broadcaster.of_type("reconnected")] == [1]

    # Nothing the service saw came close to its idle window — "comfortably
    # inside" it, with room to lose a beat entirely and still be fine.
    assert max(session.gaps) <= MAX_KEEPALIVE_SEC


async def test_without_the_keepalive_the_same_silence_still_kills_the_session(
        clock, broadcaster, state):
    """The proof that the test above can fail.

    Identical script, keep-alive switched off. The service hangs up during the
    pause and the tool only finds out when it tries to send the first word of
    the next passage — which is exactly the word the congregation loses.
    """
    first = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    second = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[first, second])

    await run_supervisor(
        audio=scripted_audio(script_with_silence(), clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": False},
    )

    assert first.idle_closed is True
    assert client.connect_count == 2

    # Four loud frames were spoken; only three ever reached the service.
    delivered = [f for f in first.frames + second.frames if f.peak > 0.1]
    assert len(delivered) == 3


async def test_captions_resume_on_the_first_word_after_the_silence(
        clock, broadcaster, state):
    """Catch the first word after the pause, not the fifth.

    The service is scripted to caption whichever frame carries the first loud
    audio after the silence. For that caption to exist at all, the frame has
    to have arrived on a live connection.
    """
    async def caption_the_first_word_after_the_pause(n, session):
        if len([f for f in session.frames if f.peak > 0.1]) == 3:
            session.emit_speech_signal("START_SPEECH")
            session.emit_transcript("and we continue")

    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC,
                          on_frame=caption_the_first_word_after_the_pause)
    client = FakeSarvamClient(sessions=[session])

    await run_supervisor(
        audio=scripted_audio(script_with_silence(), clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": True},
    )

    assert [m["text"] for m in broadcaster.of_type("final")] == ["and we continue"]

    # Every loud frame on both sides of the pause reached the service, on the
    # one connection, with no reconnect gap for a word to fall into.
    assert [p for p in session.peaks if p > 0.1] == [0.5] * 4
    assert client.connect_count == 1


async def test_keepalive_frames_put_nothing_on_the_screen(clock, broadcaster, state):
    """Five minutes of keep-alive must caption exactly nothing.

    The audio gate does not only save money — it also means a speech model is
    never handed a stretch of silence to invent words from. A keep-alive
    deliberately hands it silence, so the thing that must not happen is text
    appearing on the overlay during a pause when nobody is speaking.

    The fake service answers the silence the way silence should be answered:
    with nothing. What that pins is our half of it — fourteen keep-alive
    frames go out and the tool broadcasts no caption of its own making.

    It does NOT establish what the real service does with digital silence.
    That is a fact about Sarvam, not about this code, and it needs a live
    probe with a real key.
    """
    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[session])
    # Two bursts of speech, then nothing but a quiet room to the end.
    script = [speech()] * 2 + [room()] * int(SILENCE_SEC / FRAME_SEC)

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": True},
    )

    # The keep-alive really did run — otherwise this asserts nothing at all.
    assert len(session.silent_frames) >= 10

    # And not one word reached the overlay.
    assert broadcaster.of_type("final") == []
    assert broadcaster.of_type("partial") == []


async def test_the_gate_still_suppresses_room_noise(clock, broadcaster, state):
    """What the keep-alive costs: one frame per interval, and it is silent.

    The gate exists so the event does not pay to transcribe an empty room.
    Holding the connection open must not quietly undo that — a keep-alive
    carries digital silence, never the room noise the gate just rejected.
    """
    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[session])
    script = script_with_silence()

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": True, "keepalive_sec": DEFAULT_KEEPALIVE_SEC},
    )

    # Room noise reaches the service only during the 1.5 s hangover after the
    # last loud audio — three frames, exactly as before this change.
    assert session.peaks.count(0.003) == 3

    # Everything else sent across the pause is digital silence, and there is
    # one per keep-alive interval rather than one per frame of audio.
    assert len(session.silent_frames) <= SILENCE_SEC / DEFAULT_KEEPALIVE_SEC + 1

    # Which leaves the bill essentially unchanged: 600 frames of quiet room
    # arrived during the pause and the service was billed for a couple of
    # dozen.
    assert len(session.frames) < len(script) / 20


async def test_the_keepalive_interval_is_configuration(clock, broadcaster, state):
    """The interval is a setting, not a constant baked into the sender."""
    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[session])

    await run_supervisor(
        audio=scripted_audio(script_with_silence(), clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": True, "keepalive_sec": 5.0},
    )

    # A frame every five seconds instead of every twenty — the gap the service
    # sees can exceed the interval by at most the length of one audio frame,
    # because a keep-alive can only ride out on a frame that has arrived.
    assert max(session.gaps) <= 5.0 + FRAME_SEC
    assert len(session.silent_frames) > SILENCE_SEC / 10


@pytest.mark.parametrize("configured", [45.0, 90.0, 0.0, -5.0, "banana", None])
async def test_an_interval_that_would_not_defend_the_idle_window_is_refused(
        clock, broadcaster, state, configured):
    """A misconfigured interval must not silently reinstate the defect.

    A keep-alive rides out on an audio frame that has arrived, so the gap the
    service actually sees is the interval plus up to a frame, plus the
    network. An interval past half the idle window has too little margin left
    to be worth trusting — 45 s would look configured while leaving nothing
    spare. Rather than accept one, the tool falls back to the default.
    """
    session = FakeSession(clock, idle_timeout_sec=SERVICE_IDLE_TIMEOUT_SEC)
    client = FakeSarvamClient(sessions=[session])

    await run_supervisor(
        audio=scripted_audio(script_with_silence(90), clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        gate_cfg={"keepalive": True, "keepalive_sec": configured},
    )

    assert session.idle_closed is False
    assert client.connect_count == 1
    # The default took over — not the value that was asked for.
    assert max(session.gaps) <= DEFAULT_KEEPALIVE_SEC + FRAME_SEC


def test_the_env_vars_an_operator_sets_reach_the_tool():
    """`.env` is the configuration path a deployment actually uses.

    The module resolves both settings at import, so this drives real imports
    in a clean process rather than reaching into an already-imported module.
    """
    probe = """
import os, importlib
import live_captions as lc
for env in ({}, {"SARVAM_KEEPALIVE": "off"},
            {"SARVAM_KEEPALIVE_SEC": "12"}, {"SARVAM_KEEPALIVE_SEC": "90"}):
    for key in ("SARVAM_KEEPALIVE", "SARVAM_KEEPALIVE_SEC"):
        os.environ.pop(key, None)
    os.environ.update(env)
    importlib.reload(lc)
    print("RESULT", lc.KEEPALIVE_ENABLED, lc.KEEPALIVE_SEC)
"""
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr
    results = [line.split()[1:] for line in done.stdout.splitlines()
               if line.startswith("RESULT")]

    assert results == [
        ["True", str(DEFAULT_KEEPALIVE_SEC)],   # shipped default: on, and
        ["False", str(DEFAULT_KEEPALIVE_SEC)],  # SARVAM_KEEPALIVE=off
        ["True", "12.0"],                       # SARVAM_KEEPALIVE_SEC=12
        ["True", str(DEFAULT_KEEPALIVE_SEC)],   # 90 refused, default kept
    ]

    # The shipped default is inside the idle window with a beat to spare.
    assert 0 < DEFAULT_KEEPALIVE_SEC <= MAX_KEEPALIVE_SEC
