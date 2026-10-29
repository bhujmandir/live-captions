"""Lag watchdog: drop a Sarvam session that has fallen behind the room.

On 7 Oct the hall saw captions 10+ minutes late while every number on the
server said 5 s, and a fresh connection cured it at once. The server's latency
figure is "final arrived minus the segment's length", which cannot see a
backlog inside the speech service: a final for speech from ten minutes ago that
arrives now still looks five seconds old.

Sarvam's streaming answers carry no audio timestamps, but a backlog has one
tell that needs none: the service keeps sending finals while the tool has sent
it nothing. A healthy final lands within a few seconds of the last audio sent.
With `lag_limit_sec` in the start payload, a final arriving longer than that
after the last audio sent means the service is at least that far behind, so
the tool drops the stale words and reconnects.
"""
from __future__ import annotations

import asyncio

import live_captions

from .fakes import FRAME_SEC, FakeSarvamClient, FakeSession, scripted_audio, silence, speech


async def _run(sarvam_cfg, *, final_after_silence_sec, clock, broadcaster, state):
    talk = [speech()] * int(5 / FRAME_SEC)
    quiet = [silence()] * int(30 / FRAME_SEC)
    more = [speech()] * int(3 / FRAME_SEC)
    frames = talk + quiet + more
    sessions = [FakeSession(clock), FakeSession(clock)]
    client = FakeSarvamClient(sessions=sessions)
    emit_at = len(talk) + int(final_after_silence_sec / FRAME_SEC)

    async def audio():
        index = 0
        async for item in scripted_audio(frames, clock, broadcaster):
            if index == emit_at:
                sessions[0].emit_transcript("words from long ago")
            index += 1
            yield item

    await asyncio.wait_for(
        live_captions.sarvam_loop(
            audio(), broadcaster, False, asyncio.Event(), state,
            sarvam_cfg=sarvam_cfg, client=client),
        timeout=30.0)
    return client


def _shown(broadcaster):
    return [m.get("raw") for m in broadcaster.of_type("final")]


async def test_a_final_long_after_the_audio_stopped_means_a_backlog_so_reconnect(
        clock, broadcaster, state):
    client = await _run({"model": "saaras:v3", "lag_limit_sec": 20},
                        final_after_silence_sec=25,
                        clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 2
    assert "words from long ago" not in _shown(broadcaster)


async def test_a_final_soon_after_the_audio_is_normal(clock, broadcaster, state):
    client = await _run({"model": "saaras:v3", "lag_limit_sec": 20},
                        final_after_silence_sec=2,
                        clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 1
    assert "words from long ago" in _shown(broadcaster)


async def test_without_the_setting_nothing_changes(clock, broadcaster, state):
    client = await _run({"model": "saaras:v3"}, final_after_silence_sec=25,
                        clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 1
    assert "words from long ago" in _shown(broadcaster)


# ── Coverage: the backlog during unbroken sound ──────────────────────────
#
# Measured on the hall's logs: a healthy session finalises 79–89% of the
# audio sent to it. On 7 Oct from 09:24 it finalised 10–38% for twenty minutes
# while finals kept coming, and the captions drifted 11 minutes behind. The
# room was never silent, so only the coverage could see it.

async def _run_coverage(sarvam_cfg, *, coverage, seconds, clock, broadcaster, state):
    frames = [speech()] * int(seconds / FRAME_SEC)
    every = 10                                   # a final every 5 s of audio

    async def answer(n, session):
        if n % every == 0:
            session.emit({"type": "data", "data": {
                "transcript": f"line {n // every}",
                "metrics": {"audio_duration": every * FRAME_SEC * coverage}}})

    sessions = [FakeSession(clock, on_frame=answer), FakeSession(clock, on_frame=answer)]
    client = FakeSarvamClient(sessions=sessions)
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            scripted_audio(frames, clock, broadcaster), broadcaster, False,
            asyncio.Event(), state, sarvam_cfg=sarvam_cfg, client=client),
        timeout=60.0)
    return client


async def test_a_session_finalising_little_of_what_it_hears_is_restarted(
        clock, broadcaster, state):
    client = await _run_coverage({"model": "saaras:v3", "min_coverage": 0.4},
                                 coverage=0.10, seconds=240,
                                 clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 2


async def test_a_healthy_session_is_left_alone(clock, broadcaster, state):
    client = await _run_coverage({"model": "saaras:v3", "min_coverage": 0.4},
                                 coverage=0.85, seconds=240,
                                 clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 1


async def test_coverage_is_not_judged_on_the_first_minutes(clock, broadcaster, state):
    # 09:09 on 7 Oct sat at 18% while the session warmed up: too little audio
    # to judge, and a restart there would only cost a reconnect for nothing.
    client = await _run_coverage({"model": "saaras:v3", "min_coverage": 0.4},
                                 coverage=0.10, seconds=100,
                                 clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 1


async def test_coverage_watch_is_off_without_the_setting(clock, broadcaster, state):
    client = await _run_coverage({"model": "saaras:v3"},
                                 coverage=0.10, seconds=240,
                                 clock=clock, broadcaster=broadcaster, state=state)

    assert len(client.connect_kwargs) == 1
