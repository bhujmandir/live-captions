"""What the hall and the operator are told when the connection fails.

A frozen caption is worse than no caption: the hall reads words that do not
match what is being said, and nobody realises anything is wrong. These tests
pin down the three things that stop that happening — the link state reaching
every tab, the overlay blanking on a fault, and reconnection attempts being
spaced far enough apart that a fault cannot turn into a retry storm.

No network, no `SARVAM_API_KEY`, no audio device. Run them with:

    uv run --extra dev pytest
"""

from __future__ import annotations

import asyncio
import json
import time as real_time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import live_captions

from .fakes import FakeSarvamClient, FakeSession, scripted_audio, speech
from .fakes_connection import InstantRetryWait, TabFanout, TimedSarvamClient, idle_audio


@pytest.fixture
def floor(monkeypatch) -> float:
    """Pin the reconnect floor.

    `RECONNECT_MIN_INTERVAL_SEC` is read from the environment once, at import,
    and `live_captions` loads `.env` on import — so without this a developer
    who has tuned the knob would see these tests fail for the wrong reason.
    """
    monkeypatch.setattr(live_captions, "RECONNECT_MIN_INTERVAL_SEC", 1.0)
    return 1.0


async def run_supervisor(*, audio, broadcaster, state, client, retry_wait=None,
                         stop_event=None, timeout=30.0):
    """Run `sarvam_loop` to completion, exactly as `/api/start` does."""
    stop_event = stop_event if stop_event is not None else asyncio.Event()
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            audio, broadcaster, False, stop_event, state,
            sarvam_cfg={"model": "saaras:v3"},
            client=client, retry_wait=retry_wait,
        ),
        timeout=timeout,
    )


def link_states(messages: list[dict]) -> list[tuple[str, str | None]]:
    """Every link announcement in `messages`, as (state, reason) pairs."""
    return [(m["state"], m["reason"]) for m in messages if m.get("type") == "connection"]


async def test_a_dropped_connection_is_announced_as_a_fault_and_blanks_the_overlay(
        floor, clock, broadcaster, state):
    """The defect this ticket exists to fix, end to end.

    The service fails mid-katha the way a network fault does. Three things
    have to follow: the link is announced as lost rather than left looking
    healthy, the overlay is blanked so the hall stops reading a line that no
    longer matches anything being said, and captions resume once the
    supervisor gets back in.
    """
    async def drop_after_third_frame(n, session):
        if n == 3:
            session.drop()

    async def caption_on_first_frame(n, session):
        if n == 1:
            session.emit_transcript("and the katha continues")

    first  = FakeSession(clock, on_frame=drop_after_third_frame)
    second = FakeSession(clock, on_frame=caption_on_first_frame)
    client = FakeSarvamClient(sessions=[first, second])

    await run_supervisor(
        audio=scripted_audio([speech()] * 8, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    # The whole story of the link, in order — and `dropped` is the operator's
    # answer to "is it me or is it the network?".
    assert link_states(broadcaster.messages) == [
        ("connected",    None),
        ("disconnected", "dropped"),
        ("connected",    None),
        ("idle",         None),
    ]

    # The blanking is not advice to the browser, it is an instruction to every
    # surface on the bus — the operator's preview, the hall's overlay and any
    # sidecar display that never runs our React code.
    lost_at = next(i for i, m in enumerate(broadcaster.messages)
                   if m.get("type") == "connection" and m["state"] == "disconnected")
    assert broadcaster.messages[lost_at + 1] == {"type": "clear"}

    # The fault did not end the session.
    assert client.connect_count == 2
    assert [m["text"] for m in broadcaster.of_type("final")] == ["and the katha continues"]

    # And a tab opening after it is all over is told there is nothing running,
    # not that something is broken.
    assert state["connection"]["state"] == "idle"


async def test_a_clean_close_is_not_reported_as_a_network_fault(
        floor, clock, broadcaster, state):
    """A flip is not an outage.

    `/api/direction` closes the live connection on purpose, and so does the
    service's own idle timeout. Both come back through the same reconnect
    path, so both have to be distinguishable from a fault — otherwise the
    operator learns to ignore the one warning that matters.

    The overlay still blanks: whatever was on it was captioned under the old
    direction, or before a gap the tool cannot account for.
    """
    async def close_after_third_frame(n, session):
        if n == 3:
            session.close()

    first  = FakeSession(clock, on_frame=close_after_third_frame)
    second = FakeSession(clock)
    client = FakeSarvamClient(sessions=[first, second])

    await run_supervisor(
        audio=scripted_audio([speech()] * 8, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
    )

    assert link_states(broadcaster.messages) == [
        ("connected",    None),
        ("disconnected", "closed"),
        ("connected",    None),
        ("idle",         None),
    ]
    assert client.connect_count == 2


async def test_reconnect_attempts_are_spaced_by_a_floor_however_the_session_ended(
        floor, clock, broadcaster, state):
    """The guard against a retry storm at the vendor's rate limiter.

    Six sessions that die the instant they open: three closing cleanly, three
    failing like a network fault. The clean close is the dangerous one,
    because it deliberately resets the backoff so a flip reconnects promptly —
    which, on a service that hangs up the moment it accepts, is a loop that
    reconnects as fast as the supervisor can turn over.

    Both endings are treated alike here on purpose. What earns a reset to the
    floor is a session that lasted — not one that ended politely.

    The microphone hands over nothing for the duration, so nothing but the
    supervisor's own wait can move the clock, and the spacing measured here is
    the spacing the supervisor chose.
    """
    clean  = [FakeSession(clock) for _ in range(3)]
    faulty = [FakeSession(clock) for _ in range(3)]
    for session in clean:
        session.close()
    for session in faulty:
        session.drop()

    client     = TimedSarvamClient(clock, clean + faulty)
    waits      = InstantRetryWait(clock, stop_after=6)
    stop_event = asyncio.Event()

    started = real_time.monotonic()
    await run_supervisor(
        audio=idle_audio(stop_event),
        broadcaster=broadcaster, state=state, client=client,
        retry_wait=waits, stop_event=stop_event,
    )
    wall_clock = real_time.monotonic() - started

    assert client.connect_count == 6

    # Nothing is ever tighter than the floor — the point of measuring from one
    # attempt to the next rather than from the end of the last session.
    assert min(client.gaps) >= floor

    # And repeated failure backs OFF, whether the sessions ended cleanly or
    # faulted. Every session here dies the instant it opens, so none of them
    # earns the health reset, and the spacing must grow rather than sit on the
    # floor. A clean close is not evidence of health: a service that accepts a
    # connection and immediately closes it politely, pinned at the floor, is
    # ~3600 attempts an hour into a rate limiter — the exact failure this floor
    # exists to prevent.
    assert client.gaps == sorted(client.gaps), (
        f"spacing did not grow under sustained failure: {client.gaps}"
    )
    assert client.gaps[-1] > floor, (
        f"six straight failures never escalated past the floor: {client.gaps}"
    )

    # Six attempts' worth of spacing is virtual, so this is a test anyone will
    # actually run.
    assert wall_clock < 5.0, (
        f"six attempts took {wall_clock:.1f}s of wall clock — the retry wait "
        "is no longer virtual"
    )


async def test_every_tab_is_told_the_link_state_including_one_opened_mid_outage(
        floor, clock, state):
    """Broadcast to every connected tab, and survives a reconnect.

    A broadcast only reaches the tabs that happen to be open when it goes out.
    The operator refreshing the page during an outage — or someone opening the
    overlay on the hall's machine — has to be told the same truth, so the
    state is handed to each tab as it arrives as well as broadcast.

    This drives the real supervisor through the real `Broadcaster` and the
    real `/ws` handler over real WebSockets, so what is asserted here is what
    a browser would actually receive. Both sessions are scripted to end the
    instant they open, which keeps the microphone out of it entirely: see the
    note on the scripted source in docs/how-it-works.md.
    """
    real        = live_captions.Broadcaster()
    broadcaster = TabFanout(real)

    app = web.Application()
    app["broadcaster"] = real
    app["state"]       = state
    app.router.add_get("/ws", live_captions.handle_ws)

    tabs = TestClient(TestServer(app))
    await tabs.start_server()

    faulty, tidy = FakeSession(clock), FakeSession(clock)
    faulty.drop()
    tidy.close()
    client     = FakeSarvamClient(sessions=[faulty, tidy])
    stop_event = asyncio.Event()

    late_tab: aiohttp.ClientWebSocketResponse | None = None
    late_arrival: list[dict] = []

    async def open_a_second_tab_during_the_first_outage(seconds, stop_event):
        nonlocal late_tab
        clock.advance(seconds)
        if late_tab is None:
            late_tab = await tabs.ws_connect("/ws")
            # Reading its opening frames here is also what proves the server
            # has finished registering it, before the next broadcast goes out.
            late_arrival.extend(await drain(late_tab))
        else:
            stop_event.set()
        return stop_event.is_set()

    try:
        first_tab = await tabs.ws_connect("/ws")
        await run_supervisor(
            audio=idle_audio(stop_event),
            broadcaster=broadcaster, state=state, client=client,
            retry_wait=open_a_second_tab_during_the_first_outage,
            stop_event=stop_event,
        )

        # The tab that was open throughout was handed the state on arrival and
        # then told about every change.
        assert link_states(await drain(first_tab)) == [
            ("idle",         None),
            ("connected",    None),
            ("disconnected", "dropped"),
            ("connected",    None),
            ("disconnected", "closed"),
            ("idle",         None),
        ]

        # The tab that arrived mid-outage was told about the outage. Without
        # this it would show an empty caption bar and no reason for it, which
        # is the exact ambiguity this state exists to end.
        assert link_states(late_arrival) == [("disconnected", "dropped")]

        # And it then tracked the reconnect like any other tab.
        assert link_states(await drain(late_tab)) == [
            ("connected",    None),
            ("disconnected", "closed"),
            ("idle",         None),
        ]
    finally:
        await tabs.close()


async def drain(tab) -> list[dict]:
    """Every frame the tab has been sent so far, without waiting for more."""
    received: list[dict] = []
    while True:
        try:
            msg = await tab.receive(timeout=0.5)
        except asyncio.TimeoutError:
            break
        if msg.type is not aiohttp.WSMsgType.TEXT:
            break
        received.append(json.loads(msg.data))
    return received


async def test_a_session_that_lasted_resets_the_backoff_a_session_that_did_not_does_not(
        floor, clock, broadcaster, state):
    """What earns a prompt reconnect is a session that worked, not a polite ending.

    The distinction matters twice in one katha. A deliberate direction flip
    closes the connection on purpose after a long healthy stretch, and should
    come back immediately — nobody should wait eight seconds for captions
    because they changed a dropdown. But a service that accepts a connection
    and closes it cleanly on contact must back off, or it is retried at the
    floor for as long as the fault lasts.

    Both endings are clean. Only the duration tells them apart, which is why
    the reset is keyed on how long the session lived rather than on how it
    finished.
    """
    healthy = FakeSession(clock)
    healthy.on_frame = None
    sick = [FakeSession(clock) for _ in range(3)]
    for s in sick:
        s.close()

    # Enough speech to carry the first session past the health threshold in
    # virtual time, then it closes cleanly — the shape of a direction flip.
    frames = int(live_captions.HEALTHY_SESSION_SEC / 0.5) + 4
    # Well past what the first session consumes, so the microphone is still
    # open when the later sessions run — otherwise the supervisor stops
    # because the audio ended, not because it chose to.
    script = [speech()] * (frames + 60)

    async def close_at_end(n, session):
        if n >= frames:
            session.close()
    healthy._on_frame = close_at_end

    client = TimedSarvamClient(clock, [healthy] + sick)
    waits = InstantRetryWait(clock, stop_after=3)

    await run_supervisor(
        audio=scripted_audio(script, clock, broadcaster),
        broadcaster=broadcaster, state=state, client=client,
        retry_wait=waits, stop_event=asyncio.Event(),
    )

    assert client.connect_count >= 2, "the healthy session never reconnected"

    # Assert on the WAIT the supervisor chose, not the gap between attempts.
    # The gap includes however long the session itself ran, so a healthy
    # session of 30s produces a 30s gap no matter what the backoff did — the
    # wait is the only figure that reflects the decision under test.
    assert waits.delays, "the supervisor never waited between attempts"

    # Straight after the long healthy session, the wait is the floor: the
    # backoff was reset because that session had done its job. This is the
    # direction-flip case — nobody waits eight seconds for captions because
    # they changed a dropdown.
    # A prompt reconnect is the behaviour; a recorded wait of exactly the
    # floor is only one of the shapes it takes. When the healthy session has
    # already outlasted the floor, nothing is owed and the supervisor waits
    # not at all — which is the ideal outcome, and was being reported as a
    # failure on the machine where it actually happened (mandir PC, #27).
    # What must never appear here is a grown backoff, which would be seconds.
    assert waits.delays[0] <= floor + 1e-6, (
        f"a session that ran for {live_captions.HEALTHY_SESSION_SEC}s did not "
        f"reset the backoff — first wait was {waits.delays[0]}, floor is {floor}"
    )

    # These two are where this test's real discriminating power sits. A
    # single clean close moves the backoff to the floor whether or not the
    # reset fired, so the first wait alone cannot tell them apart; the shape
    # of the escalation that follows can.
    assert waits.delays[-1] > floor, (
        f"sessions dying on contact never escalated: {waits.delays}"
    )
    assert waits.delays == sorted(waits.delays), (
        f"waits did not grow under sustained failure: {waits.delays}"
    )
