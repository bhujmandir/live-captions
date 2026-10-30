"""Forced flush: a caption for long unbroken speech, without waiting for a pause.

Saaras finalises a segment only when the speaker pauses. A swami who talks
for 12s without one puts the first words of that segment on the hall screen
12s+ late — measured on 6 Oct at p90 7s and max 13.5s from speech start to
translated line. With `force_flush_sec` set in the start payload, the tool asks
the service to finalise what it has every N seconds of speech with no final.
"""
from __future__ import annotations

import asyncio

import live_captions

from .fakes import FRAME_SEC, FakeSarvamClient, FakeSession, scripted_audio, speech


class StreamingFlushSession(FakeSession):
    """A session that, connected with flush_signal, finalises on flush and stays open."""

    def __init__(self, clock, *, total_frames: int, **kw):
        super().__init__(clock, **kw)
        self.total_frames = total_frames
        self.flush_times: list[float] = []

    async def flush(self) -> None:
        self.flush_times.append(self.clock.now)
        self.emit_transcript(f"fragment {len(self.flush_times)}")
        if len(self.frames) >= self.total_frames:
            self.close()      # the end-of-audio flush ends the session, as before


async def _run(sarvam_cfg, seconds, clock, broadcaster, state):
    async def speech_starts(n, session):
        if n == 1:
            session.emit_speech_signal("START_SPEECH")

    frames = [speech()] * int(seconds / FRAME_SEC)
    session = StreamingFlushSession(clock, on_frame=speech_starts,
                                    total_frames=len(frames))
    client = FakeSarvamClient(sessions=[session])
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            scripted_audio(frames, clock, broadcaster), broadcaster, False,
            asyncio.Event(), state, sarvam_cfg=sarvam_cfg, client=client),
        timeout=30.0)
    return session, client


async def test_unbroken_speech_is_flushed_every_n_seconds(clock, broadcaster, state):
    session, client = await _run({"model": "saaras:v4", "force_flush_sec": 4.0},
                                 12.0, clock, broadcaster, state)

    assert client.connect_kwargs[0]["flush_signal"] is True
    assert "force_flush_sec" not in client.connect_kwargs[0]
    t0 = session.frames[0].at
    # Mid-speech flushes only; the last one is the existing end-of-audio flush.
    # Each comes N s after the previous cut (speech start, final or flush),
    # give or take a frame for the service's answer to arrive.
    cuts = [t0] + session.flush_times[:-1]
    gaps = [round(b - a, 1) for a, b in zip(cuts, cuts[1:])]
    assert len(gaps) == 2
    assert all(4.0 <= g <= 4.0 + 2 * FRAME_SEC for g in gaps), gaps


async def test_without_the_setting_nothing_changes(clock, broadcaster, state):
    session, client = await _run({"model": "saaras:v4"}, 12.0, clock, broadcaster, state)

    assert "flush_signal" not in client.connect_kwargs[0]
    assert len(session.flush_times) == 1          # only the end-of-audio flush


async def test_a_flushed_fragment_goes_out_at_once_not_held_for_a_sentence(
        clock, broadcaster, state):
    # A forced fragment rarely ends on punctuation, so the sentence assembler
    # would hold it for its quiet window — and the swami is not quiet. Holding
    # it would spend the time the flush was there to save.
    await _run({"model": "saaras:v3", "force_flush_sec": 4.0},
               12.0, clock, broadcaster, state)

    raws = [m.get("raw") for m in broadcaster.of_type("final")]
    assert raws[:2] == ["fragment 1", "fragment 2"]


async def test_a_bad_value_is_off_not_a_crash_or_a_flush_every_frame(
        clock, broadcaster, state):
    for bad in (-3, "4s", None):
        session, client = await _run({"model": "saaras:v4", "force_flush_sec": bad},
                                     6.0, clock, broadcaster, state)
        assert "flush_signal" not in client.connect_kwargs[0], bad
        assert len(session.flush_times) == 1, bad      # only the end-of-audio flush
