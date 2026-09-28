"""A slow translator must cost a late caption, never the speaker's words.

The sentence emitter translates over the network. It was being awaited inside
the audio sender loop, so a slow translate stalled audio capture: the device
queue holds ~4 seconds and drops the OLDEST frame on overflow, so speech was
discarded before it was ever transcribed — absent from the record, with
nothing in the output showing a gap.

That is the worst failure this tool has, because it destroys the evidence of
itself. A 1.5-hour katha with a real audience is exactly the run it corrupts.
"""
import asyncio

import pytest

import live_captions
from tests.fakes import (FakeBroadcaster, FakeSarvamClient, FakeSession,
                         scripted_audio, speech)


class SlowFinalBroadcaster(FakeBroadcaster):
    """Broadcasts normally, except that publishing a caption takes ages.

    Only `final` is slowed. `level` — which paces the scripted microphone —
    stays fast, so the test measures the emitter's effect on audio capture
    and nothing else.
    """

    def __init__(self, final_delay_sec: float):
        super().__init__()
        self.final_delay_sec = final_delay_sec

    async def send(self, msg: dict) -> None:
        if msg.get("type") == "final":
            await asyncio.sleep(self.final_delay_sec)
        await super().send(msg)


@pytest.fixture
def broadcaster() -> SlowFinalBroadcaster:
    # 0.4s of real time per caption — far longer than the frame cadence, and
    # standing in for the 7.5s worst case (Gemini timeout then Mayura).
    return SlowFinalBroadcaster(final_delay_sec=0.4)


async def live_microphone(frames, clock, frame_sec=0.5, real_gap=0.015):
    """An UNPACED source, the way a real microphone behaves.

    The scripted source in `fakes.py` waits for the tool to process each
    frame before handing over the next, which is what makes it
    deterministic — but it also means the tool can never fall behind it. A
    real capture device does not wait for anyone: it pushes frames at
    wall-clock rate into a bounded queue, and if the consumer stalls, the
    oldest audio is discarded. That is the condition this defect needs.
    """
    for pcm in frames:
        clock.advance(frame_sec)
        yield pcm, clock.now
        await asyncio.sleep(real_gap)


async def test_a_slow_caption_does_not_cost_us_a_single_frame_of_audio(
        clock, broadcaster, state):
    """Every frame the microphone produced must still reach the service.

    The sentence here is a short fragment with no full stop, so it is HELD
    and released by the quiet timer — which runs in the audio sender. That
    is the path where a slow emitter stalls capture.
    """
    async def talk(n, session):
        if n == 2:
            session.emit_transcript("bhagwan ni")     # held: short, unpunctuated

    session = FakeSession(clock, on_frame=talk)
    client = FakeSarvamClient(sessions=[session])
    script = [speech()] * 30            # ~2x the 16-frame queue

    stop_event = asyncio.Event()
    await live_captions.sarvam_loop(
        live_microphone(script, clock), broadcaster, False,
        stop_event, state, sarvam_cfg={"model": "saaras:v3"}, client=client,
    )

    lost = len(script) - len(session.frames)
    assert lost == 0, (
        f"a slow caption cost us {lost} frame(s) of the speaker's audio "
        f"({lost * 0.5:.1f}s) — the exact failure this test exists for"
    )


async def test_captions_still_arrive_in_the_order_they_were_spoken(
        clock, broadcaster, state):
    """Decoupling must not let a fast line overtake a slow one."""
    async def talk(n, session):
        if n in (2, 4, 6):
            session.emit_transcript(f"sentence {n} said in full here.")

    session = FakeSession(clock, on_frame=talk)
    client = FakeSarvamClient(sessions=[session])

    stop_event = asyncio.Event()
    await live_captions.sarvam_loop(
        scripted_audio([speech()] * 12, clock, broadcaster), broadcaster,
        False, stop_event, state, sarvam_cfg={"model": "saaras:v3"},
        client=client,
    )

    finals = [m["text"] for m in broadcaster.of_type("final")]
    assert finals == sorted(finals, key=lambda t: int(t.split()[1])), (
        f"captions arrived out of order: {finals}"
    )


async def test_every_sentence_spoken_is_still_published(
        clock, broadcaster, state):
    """Decoupling must not silently drop the queued tail on shutdown."""
    async def talk(n, session):
        if n in (2, 4, 6):
            session.emit_transcript(f"sentence {n} said in full here.")

    session = FakeSession(clock, on_frame=talk)
    client = FakeSarvamClient(sessions=[session])

    stop_event = asyncio.Event()
    await live_captions.sarvam_loop(
        scripted_audio([speech()] * 12, clock, broadcaster), broadcaster,
        False, stop_event, state, sarvam_cfg={"model": "saaras:v3"},
        client=client,
    )
    assert len(broadcaster.of_type("final")) == 3
