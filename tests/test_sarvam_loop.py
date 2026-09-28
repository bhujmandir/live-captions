"""End-to-end tests for the session supervisor, `sarvam_loop`.

These drive the real supervisor with a scripted audio source and a fake speech
service. They assert only what is externally observable — what the tool sends
to the service, what it broadcasts to browser tabs, and when — never internal
call sequences or private state.

No network, no `SARVAM_API_KEY`, no audio device. Run them with:

    uv run --extra dev pytest
"""

from __future__ import annotations

import asyncio
import time as real_time

import live_captions

from .fakes import (
    FRAME_SEC,
    FakeSarvamClient,
    FakeSession,
    just_above_gate,
    just_below_gate,
    room,
    scripted_audio,
    speech,
)



async def run_supervisor(*, audio, broadcaster, state, client=None, timeout=30.0):
    """Run `sarvam_loop` to completion, exactly as `/api/start` does.

    `client=None` is the production path — the supervisor builds its own.
    """
    stop_event = asyncio.Event()
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            audio, broadcaster, False, stop_event, state,
            sarvam_cfg={"model": "saaras:v3"},
            client=client,
        ),
        timeout=timeout,
    )


async def test_speech_is_captioned_and_silence_is_gated(clock, broadcaster, state):
    """The whole path, end to end: audio in, captions out.

    The script is a realistic fragment of a live event — a few seconds of
    speech, a pause, then speech again:

        speech ×2 · quiet room ×10 (5 s) · speech ×2

    and it pins down three things at once: which frames reach the service,
    which captions reach the browser, and how long the tool goes silent
    across a pause.
    """
    async def answer_after_second_frame(n, session):
        if n == 2:
            session.emit_speech_signal("START_SPEECH")
            session.emit_transcript("welcome back everyone")
            session.emit_speech_signal("END_SPEECH")

    session = FakeSession(clock, on_frame=answer_after_second_frame)
    client = FakeSarvamClient(sessions=[session])
    script = [speech(), speech()] + [room()] * 10 + [speech(), speech()]

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    # ── what was sent ────────────────────────────────────────────────────
    # Both bursts of speech reach the service, and so do the first three
    # quiet frames: the gate holds open for a 1.5 s hangover after the last
    # loud audio so the tail of a sentence is not clipped. Everything after
    # that is dropped locally — the point of the gate is not paying to
    # transcribe an empty room.
    assert len(session.frames) == 7
    assert session.peaks == [0.5, 0.5, 0.003, 0.003, 0.003, 0.5, 0.5]

    # Every frame is announced the way the service's protocol requires.
    assert {f.encoding for f in session.frames} == {"audio/wav"}
    assert {f.sample_rate for f in session.frames} == {16000}

    # ── when ─────────────────────────────────────────────────────────────
    # Relative to the first frame, in seconds of audio.
    t0 = session.frames[0].at
    assert [round(f.at - t0, 3) for f in session.frames] == [
        0.0, 0.5,             # speech
        1.0, 1.5, 2.0,        # hangover tail
        6.0, 6.5,             # speech resumes
    ]

    # This is the defect the rest of the spec exists to fix: across a pause of
    # only five seconds the tool sends the service nothing for four of them.
    # #11 adds a keep-alive frame and will bring this gap down to the
    # keep-alive interval — when it does, this number is expected to change,
    # and that is the test doing its job.
    assert max(session.gaps) == 4.0

    # ── what was broadcast ───────────────────────────────────────────────
    # The connection pill, the two speech boundaries, and the caption.
    assert broadcaster.types()[0] == "reconnected"
    assert broadcaster.of_type("reconnected")[0]["attempt"] == 1

    assert [m["text"] for m in broadcaster.of_type("partial")] == ["…", ""]

    finals = broadcaster.of_type("final")
    assert len(finals) == 1
    assert finals[0]["text"] == "welcome back everyone"
    assert finals[0]["raw"] == "welcome back everyone"
    assert finals[0]["rules_fired"] == []
    assert finals[0]["target_lang"] == "en"

    # One `level` message per audio frame keeps the operator's meter live,
    # gated frames included — a silent room still moves the needle.
    assert broadcaster.level_count == len(script)

    # Shutting down blanks the overlay rather than leaving the last line up.
    assert broadcaster.types()[-2:] == ["stopped", "clear"]

    # ── how it connected ─────────────────────────────────────────────────
    assert client.connect_count == 1
    assert client.connect_kwargs[0]["mode"] == "translate"     # Indic → English
    assert client.connect_kwargs[0]["language_code"] == "gu-IN"
    assert client.connect_kwargs[0]["model"] == "saaras:v3"
    assert session.flushed is True


async def test_the_gate_forwards_audio_either_side_of_its_threshold(
        clock, broadcaster, state):
    """Where the silence gate's boundary actually sits.

    The gate is what starves the connection during a pause, so *how quiet*
    counts as quiet is load-bearing — and a test scripted only with loud
    speech and near-silence would stay green if the threshold moved by a
    factor of forty. These two frames bracket the configured 1% threshold as
    closely as the level meter can resolve.
    """
    session = FakeSession(clock)
    client = FakeSarvamClient(sessions=[session])
    # One loud frame to open the gate, then long enough quiet for the 1.5 s
    # hangover to lapse, then the two frames under test.
    script = [speech()] + [room()] * 4 + [just_below_gate(), just_above_gate()]

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    # The quietest frame above the threshold reopens the gate; the loudest
    # frame below it does not.
    assert session.peaks == [0.5, 0.003, 0.003, 0.003, 0.012]


async def test_dropped_connection_reconnects_and_keeps_captioning(
        clock, broadcaster, state):
    """A network fault mid-event must not end the session.

    The service drops after the third frame. The supervisor is expected to
    open a fresh connection, on the same direction, and carry on sending the
    audio that is still arriving — the microphone is not reopened.
    """
    async def drop_after_third_frame(n, session):
        if n == 3:
            session.drop()

    async def answer_on_first_frame(n, session):
        if n == 1:
            session.emit_transcript("second half")

    first = FakeSession(clock, on_frame=drop_after_third_frame)
    second = FakeSession(clock, on_frame=answer_on_first_frame)
    client = FakeSarvamClient(sessions=[first, second])
    script = [speech()] * 8

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    assert client.connect_count == 2
    assert client.connect_kwargs[0] == client.connect_kwargs[1]

    # Every frame still reaches the service — the audio captured during the
    # reconnect is buffered, not thrown away.
    assert (len(first.frames), len(second.frames)) == (3, 5)
    assert len(first.frames) + len(second.frames) == len(script)

    # Audio keeps moving forward across the reconnect; nothing is replayed.
    assert second.frames[0].at > first.frames[-1].at

    # The operator sees a second connection attempt, and captions resume.
    assert [m["attempt"] for m in broadcaster.of_type("reconnected")] == [1, 2]
    assert [m["text"] for m in broadcaster.of_type("final")] == ["second half"]


async def test_a_five_minute_silence_costs_five_minutes_of_audio_not_of_wall_clock(
        clock, broadcaster, state):
    """The behaviour the whole spec turns on, and the proof the harness scales.

    A long event contains gaps far longer than a minute — music, a reading, a
    break. Scripted here as five minutes of quiet room between two bursts of
    speech.

    Two things are being shown. First, that time is virtual: five minutes of
    audio timeline runs in about a second of wall clock, so a test of a long
    silence is a test anyone will actually run. Second, that across the pause
    the tool sends a keep-alive rather than nothing, so the connection is
    never idle long enough for the service to close it.

    The assertions below are shaped by what this test used to prove. Before
    #11 the tool sent nothing at all for the whole pause and the gap here was
    the length of the silence; the count and the peak value are what stop that
    regressing in either direction — too many frames means the gate stopped
    gating, room noise in the middle means it is forwarding the room, and a
    wider gap means the connection is being left to idle out again.
    """
    five_minutes = int(5 * 60 / FRAME_SEC)

    session = FakeSession(clock)
    client = FakeSarvamClient(sessions=[session])
    script = [speech()] * 2 + [room()] * five_minutes + [speech()] * 2

    started = real_time.monotonic()
    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )
    wall_clock = real_time.monotonic() - started

    audio_seconds = (len(script) - 1) * FRAME_SEC
    assert audio_seconds > 300
    assert wall_clock < audio_seconds / 10, (
        f"{audio_seconds:.0f}s of audio took {wall_clock:.1f}s of wall clock — "
        "the clock is no longer virtual"
    )

    # Speech either side of the pause, and the 1.5 s hangover tail of room
    # noise after the first burst — three frames, no more.
    assert session.peaks[:5] == [0.5, 0.5, 0.003, 0.003, 0.003]
    assert session.peaks[-2:] == [0.5, 0.5]

    # Everything in between is a keep-alive: digital silence, never the room.
    middle = session.peaks[5:-2]
    assert set(middle) == {0.0}

    # And there is one per keep-alive interval, not one per frame of audio.
    # 600 frames of quiet room arrived during the pause; a handful were sent.
    assert len(middle) <= 5 * 60 / live_captions.KEEPALIVE_SEC + 2
    assert max(session.gaps) <= live_captions.KEEPALIVE_SEC
    assert client.connect_count == 1


async def test_real_client_is_constructed_when_none_is_injected(monkeypatch, clock,
                                                                broadcaster, state):
    """Injection is a test seam, not a change of production behaviour.

    With no client passed, the supervisor must still build the real
    `AsyncSarvamAI` from the environment's API key, exactly as before.
    """
    import sarvamai

    built: list[dict] = []
    session = FakeSession(clock)
    session.close()          # the service hangs up as soon as the tool connects

    def fake_constructor(**kwargs):
        built.append(kwargs)
        return FakeSarvamClient(sessions=[session])

    monkeypatch.setenv("SARVAM_API_KEY", "key-from-the-environment")
    monkeypatch.setattr(sarvamai, "AsyncSarvamAI", fake_constructor)

    await run_supervisor(
        audio=scripted_audio([], clock, broadcaster),
        broadcaster=broadcaster, state=state,
    )

    assert built == [{"api_subscription_key": "key-from-the-environment"}]


async def test_without_a_key_or_a_client_the_supervisor_refuses_to_start(
        clock, broadcaster, state, caplog):
    """The missing-key guard still applies to the production path."""
    await run_supervisor(
        audio=scripted_audio([], clock, broadcaster),
        broadcaster=broadcaster, state=state,
    )

    assert "SARVAM_API_KEY not set" in caplog.text
    # It bailed before doing anything at all — no shutdown broadcast either.
    assert broadcaster.messages == []
