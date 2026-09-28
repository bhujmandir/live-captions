"""`/api/overlay-status` has to answer "is a capture actually running?".

The reader is a person on the mandir PC inside vMix with no devtools, no
access to this code, and one call available to them:

    curl -s http://localhost:8765/api/overlay-status

Before the capture block existed, the response described only what the
caption SURFACES said about themselves. If the overlay is healthy and the
AUDIO has stopped, every field in it reads fine and the hall still has no
subtitles — which is how a 37-second caption gap once went undiagnosed from
outside. These tests hold the endpoint to the harder question.

Two halves, and both are load-bearing:

* the verdict tests drive the handler over hand-built state, because the
  states worth asserting (a mic that died 90 seconds ago) cannot be produced
  on demand from a real audio device;
* `test_the_real_capture_path_is_what_fills_the_board` drives the REAL
  supervisor and asserts the same fields. Without it, every test here would
  pass against fields nothing in the pipeline ever writes — an invented
  field that always reads healthy is the exact fault this endpoint exists to
  prevent.

Time is virtual throughout, per the house style: the `clock` fixture is
installed over `live_captions.time`, and the handler reads its "seconds ago"
figures from the same clock the test advances.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import live_captions

from .fakes import (
    FakeBroadcaster,
    FakeSarvamClient,
    FakeSession,
    room,
    scripted_audio,
    speech,
)
from .test_emit_does_not_block_audio import live_microphone


class _StubRequest:
    """Just enough of `web.Request` for the handler: its app."""

    def __init__(self, app):
        self.app = app


async def status(state) -> dict:
    """Call the endpoint over the given session state and parse what it sent.

    Reads the serialised body rather than an internal dict, so a field that
    cannot survive `json.dumps` — an `asyncio.Queue` leaking into the
    response, say — fails here rather than on the mandir PC.
    """
    app = {} if state is _MISSING else {"state": state}
    response = await live_captions.handle_overlay_status(_StubRequest(app))
    assert response.status == 200, f"status endpoint returned {response.status}"
    return json.loads(response.body)


_MISSING = object()


def running_state(clock, *, audio_ago=0.5, loud_ago=1.0, final_ago=3.0,
                  running_for=120.0, **overrides) -> dict:
    """A session that has been capturing happily for two minutes."""
    now = clock.now

    def at(ago):
        return None if ago is None else now - ago

    state = {
        "capture_running":    True,
        "capture_started_at": now - running_for,
        "caption_task":       None,
        "audio_source":       "device",
        "device":             "2",
        "file":               None,
        "source":             "gu-IN",
        "target":             "en-IN",
        "last_audio_at":      at(audio_ago),
        "last_loud_audio_at": at(loud_ago),
        "last_final_at":      at(final_ago),
        "last_audio_level":   0.31,
        "connection":         {"type": "connection", "state": "connected",
                               "attempt": 1, "reason": None,
                               "retry_in_sec": None},
        "capture_queue_max":  16,
        "capture_queue_drops": 0,
        "session_queue_drops": 0,
    }
    state.update(overrides)
    return state


# ── the verdict ──────────────────────────────────────────────────────────────

async def test_capture_running_and_recently_fed_reads_capturing(clock):
    body = await status(running_state(clock))

    assert body["verdict"] == "capturing"
    assert body["capture"]["running"] is True
    # The reader must not have to interpret anything else to act.
    assert "device 2" in body["why"]
    assert body["capture"]["last_audio_sec_ago"] == 0.5
    assert body["capture"]["last_final_sec_ago"] == 3.0


async def test_capture_running_but_no_audio_for_a_long_time_reads_silent(clock):
    """The fault that produced the caption gap: the overlay is fine, the
    audio is not. The old response could not tell these apart."""
    body = await status(running_state(clock, audio_ago=90.0, loud_ago=95.0))

    assert body["verdict"] == "silent"
    assert body["capture"]["last_audio_sec_ago"] == 90.0
    # Names the thing to go and check, not just the number.
    assert "stopped delivering" in body["why"]


async def test_audio_arriving_but_all_of_it_silent_reads_silent(clock):
    """A muted feed delivers a flawless stream of digital silence.

    Every input-side counter stays healthy — chunks arriving, queue empty,
    nothing dropped — and the hall gets nothing. Only the loud-audio marker
    separates this from a working capture.
    """
    body = await status(running_state(clock, audio_ago=0.5, loud_ago=120.0))

    assert body["verdict"] == "silent"
    assert body["capture"]["last_audio_sec_ago"] == 0.5
    assert body["capture"]["last_loud_audio_sec_ago"] == 120.0
    assert "muted" in body["why"]


async def test_capture_running_that_has_never_seen_a_frame_reads_silent(clock):
    """Start pressed, wrong device chosen: it never captures anything.

    `last_audio_sec_ago` is null rather than a large number, because "never"
    and "a long time ago" are different diagnoses.
    """
    body = await status(running_state(clock, audio_ago=None, loud_ago=None,
                                      final_ago=None, running_for=45.0))

    assert body["verdict"] == "silent"
    assert body["capture"]["last_audio_sec_ago"] is None
    assert "NOT ONE audio chunk" in body["why"]
    assert "45.0s" in body["why"]


async def test_nothing_running_reads_not_running(clock):
    body = await status({"source": "gu-IN", "target": "en-IN",
                         "capture_running": False,
                         "capture_ended_at": clock.now - 30.0})

    assert body["verdict"] == "not running"
    assert body["capture"]["running"] is False
    assert body["capture"]["stopped_sec_ago"] == 30.0
    assert "Start" in body["why"]


async def test_a_finished_caption_task_is_not_running_however_the_flag_reads(clock):
    """The task handle is the second opinion.

    `capture_running` is cleared in the supervisor's `finally`, so the two
    can only disagree if that never ran — a hard kill of the task. Believing
    the flag alone would then report a capture that has been dead for hours.
    """
    done: asyncio.Future = asyncio.get_running_loop().create_future()
    done.set_result(None)

    body = await status(running_state(clock, caption_task=done))

    assert body["verdict"] == "not running"


# ── every "when" carries a "how long ago" ────────────────────────────────────

async def test_every_last_x_field_is_reported_as_seconds_ago(clock):
    """A raw epoch timestamp is not an answer to someone reading JSON on a
    phone at the back of a mandir."""
    body = await status(running_state(clock, audio_ago=0.4, loud_ago=2.25,
                                      final_ago=41.2))
    capture = body["capture"]

    assert capture["last_audio_sec_ago"] == 0.4
    assert capture["last_loud_audio_sec_ago"] == 2.2
    assert capture["last_final_sec_ago"] == 41.2

    # And no bare epoch left in its place for the reader to subtract.
    for key, value in capture.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert value < 1_000_000_000, (
                f"{key}={value} looks like a raw epoch timestamp — the reader "
                "should never have to do the arithmetic"
            )


async def test_seconds_ago_advances_with_the_clock(clock):
    """Proves the figure is derived at read time, not stamped once."""
    state = running_state(clock, audio_ago=1.0)
    assert (await status(state))["capture"]["last_audio_sec_ago"] == 1.0

    clock.advance(60.0)
    assert (await status(state))["capture"]["last_audio_sec_ago"] == 61.0


async def test_a_long_caption_gap_is_named_even_while_capture_is_healthy(clock):
    """Audio is fine, the link is up, and captions have stopped anyway.

    The capture verdict stays honest — the input side really is working — and
    `why` sends the reader to the half of the pipeline that is not.
    """
    body = await status(running_state(
        clock, audio_ago=0.5, loud_ago=1.0, final_ago=62.0,
        connection={"state": "connected", "reason": None, "attempt": 1},
    ))

    assert body["verdict"] == "capturing"
    assert "62.0s" in body["why"]
    assert "the fault is after capture" in body["why"]
    assert body["capture"]["link_state"] == "connected"


# ── it must never be able to take the server down ────────────────────────────

async def test_cold_start_state_still_returns_valid_json(clock):
    """The process has booted and nobody has pressed anything yet."""
    body = await status({})

    assert body["verdict"] == "not running"
    assert body["capture"]["running"] is False
    assert body["capture"]["last_audio_sec_ago"] is None
    assert body["surfaces"] == []


async def test_state_of_none_still_returns_valid_json(clock):
    assert (await status(None))["verdict"] == "not running"


async def test_no_state_key_at_all_still_returns_valid_json(clock):
    assert (await status(_MISSING))["verdict"] == "not running"


async def test_garbage_in_the_state_does_not_500(clock):
    """A diagnostics path that can crash the process it reports on is worse
    than no diagnostics path. Every field here is the wrong type."""
    body = await status({
        "capture_running":     True,
        "capture_started_at":  "not a number",
        "last_audio_at":       "yesterday",
        "last_loud_audio_at":  None,
        "last_final_at":       object(),
        "connection":          "connected",
        "capture_queue":       object(),        # no .qsize()
        "capture_queue_drops": None,
        "session_queue_drops": None,
        "caption_task":        "not a task",
    })

    assert body["verdict"] in {"capturing", "silent", "not running", "unknown"}
    assert body["capture"]["queue_depth"] is None
    assert body["capture"]["dropped_chunks"] == 0


async def test_a_malformed_surface_report_does_not_cost_the_capture_verdict(clock):
    """The two halves of the board are independent on purpose: a browser
    posting nonsense must not take the audio answer away."""
    live_captions._overlay_reports.clear()
    live_captions._overlay_reports["overlay-1"] = {
        "at": clock.now, "state": "not a dict", "on_screen": "hello", "shown": [],
    }
    try:
        body = await status(running_state(clock))
    finally:
        live_captions._overlay_reports.clear()

    assert body["verdict"] == "capturing"
    assert len(body["surfaces"]) == 1
    assert body["surfaces"][0]["on_screen"] == "hello"


async def test_a_clock_that_stepped_backwards_reports_zero_not_a_negative(clock):
    body = await status(running_state(clock, audio_ago=-5.0, loud_ago=-5.0))

    assert body["capture"]["last_audio_sec_ago"] == 0.0
    assert body["verdict"] == "capturing"


# ── over real HTTP, on the real route ────────────────────────────────────────

async def test_the_registered_route_answers_a_real_curl(clock, state):
    """What the mandir PC actually runs. Proves the route is wired and the
    whole response survives serialisation over the wire."""
    app = web.Application()
    app["state"] = running_state(clock)
    app.router.add_get("/api/overlay-status", live_captions.handle_overlay_status)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/overlay-status")
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()

    assert body["verdict"] == "capturing"
    assert isinstance(body["why"], str) and body["why"]
    assert "capture" in body and "surfaces" in body


# ── and the fields are the ones the real pipeline writes ─────────────────────

async def test_the_real_capture_path_is_what_fills_the_board(clock, broadcaster, state):
    """Drive the real supervisor and read the board off the state it keeps.

    🔴 This is the test that stops the rest of the file being a fiction. Every
    other case here builds its own state dict; if nothing in the capture path
    ever wrote these keys, they would all still pass and the endpoint would
    report a permanently healthy silence.

    The verdict is sampled DURING the run — once before any caption has been
    published, and once after — because the interesting states only exist
    while audio is moving.
    """
    verdicts: list[tuple[str, str, str, dict]] = []

    async def sample_the_board(n, session):
        if n == 2:
            session.emit_speech_signal("START_SPEECH")
            session.emit_transcript("welcome back everyone to the katha today")
            session.emit_speech_signal("END_SPEECH")
        body = await status(state)
        verdicts.append((f"frame-{n}", body["verdict"], body["why"],
                         dict(body["capture"])))

    session = FakeSession(clock, on_frame=sample_the_board)
    client = FakeSarvamClient(sessions=[session])
    # Speech, then a pause long enough to release the held sentence, then
    # speech again — the shape a real reading has.
    script = [speech(), speech()] + [room()] * 10 + [speech(), speech()]

    stop_event = asyncio.Event()
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            scripted_audio(script, clock, broadcaster), broadcaster, False,
            stop_event, state, sarvam_cfg={"model": "saaras:v3"}, client=client,
        ),
        timeout=30.0,
    )

    # The capture path wrote every field the board reads. Asserted from the
    # samples taken mid-run, not from a dict this test built.
    assert verdicts, "the board was never sampled — did the audio script run?"
    first = verdicts[0][3]
    assert first["running"] is True
    assert first["last_audio_sec_ago"] is not None, (
        "the audio pump is not recording when it last saw a chunk"
    )
    assert first["last_loud_audio_sec_ago"] is not None, (
        "the sender is not recording when it last heard something above the gate"
    )
    assert first["queue_max"] == 16
    assert first["dropped_chunks"] == 0

    # Speech is being heard, so the verdict is `capturing` throughout the
    # loud frames.
    assert verdicts[0][1] == "capturing", verdicts[0][2]

    # A caption really was published, and the board saw it happen.
    assert any(c["last_final_sec_ago"] is not None for _, _, _, c in verdicts), (
        "no sample ever showed a published caption — the FINAL path is not "
        "recording when it last published"
    )

    # And once the supervisor has left, the board stops claiming to capture.
    after = await status(state)
    assert after["verdict"] == "not running"
    assert after["capture"]["queue_depth"] is None, (
        "the queue reference outlived the session — its depth means nothing "
        "once nothing is feeding it"
    )


async def test_a_new_session_does_not_inherit_the_last_one_s_timestamps(clock,
                                                                        broadcaster,
                                                                        state):
    """The nastiest version of this fault: a session that has never seen a
    frame reading as freshly fed, because the numbers are the previous
    katha's."""
    state.update({
        "last_audio_at":      clock.now,
        "last_loud_audio_at": clock.now,
        "last_final_at":      clock.now,
        "capture_queue_drops": 17,
        "session_queue_drops": 4,
    })

    async def idle_audio():
        # Nothing to hand over; end immediately so the supervisor unwinds.
        stop_event.set()
        return
        yield  # pragma: no cover - makes this an async generator

    stop_event = asyncio.Event()

    async def watch(seconds, ev):
        return True

    session = FakeSession(clock)
    session.close()
    await asyncio.wait_for(
        live_captions.sarvam_loop(
            idle_audio(), broadcaster, False, stop_event, state,
            sarvam_cfg={"model": "saaras:v3"},
            client=FakeSarvamClient(sessions=[session]), retry_wait=watch,
        ),
        timeout=30.0,
    )
    seen = state

    assert seen["last_audio_at"] is None
    assert seen["last_loud_audio_at"] is None
    assert seen["last_final_at"] is None
    assert seen["capture_queue_drops"] == 0
    assert seen["session_queue_drops"] == 0


async def test_a_dropped_chunk_is_counted_where_the_reader_can_see_it(clock, state):
    """The audio queues drop the OLDEST chunk under backpressure, and until
    now said so only in a log line on an unattended PC.

    A drop count on the board is what turns "captions felt patchy" into a
    number. This forces the condition the honest way: a real microphone does
    not wait for anyone, so it is driven unpaced while the speech service
    holds the sender still.
    """
    broadcaster = FakeBroadcaster()
    stalled = asyncio.Event()

    async def hold_the_sender(n, session):
        if n == 1:
            await stalled.wait()

    async def release_after(delay):
        await asyncio.sleep(delay)
        stalled.set()

    session = FakeSession(clock, on_frame=hold_the_sender)
    client = FakeSarvamClient(sessions=[session])
    releaser = asyncio.create_task(release_after(0.5))

    stop_event = asyncio.Event()
    try:
        await asyncio.wait_for(
            live_captions.sarvam_loop(
                live_microphone([speech()] * 60, clock, real_gap=0.002),
                broadcaster, False, stop_event, state,
                sarvam_cfg={"model": "saaras:v3"}, client=client,
            ),
            timeout=30.0,
        )
    finally:
        stalled.set()
        await releaser

    assert state["session_queue_drops"] > 0, (
        "a stalled sender overflowed the session queue and nothing counted it"
    )
    body = await status(state)
    assert body["capture"]["dropped_chunks"] == state["session_queue_drops"]
    assert body["capture"]["dropped_session_queue"] == state["session_queue_drops"]


async def test_a_dead_speech_link_is_not_reported_as_a_dead_microphone(clock, state):
    """Found by driving the real endpoint with a bad API key, not by reading
    the code.

    🔴 Loudness used to be measured in the sender, which only runs inside a
    live speech-service session. With the link down the sender never ran, so
    a microphone delivering twenty seconds of loud tone reported "nothing
    above the silence gate" and the verdict read `silent` — sending the
    person on the mandir PC to the mixing desk for a fault that was in the
    network. The capture side must describe the INPUT, whatever is or is not
    reachable downstream.
    """
    broadcaster = FakeBroadcaster()
    boards: list[dict] = []

    # Every session drops the moment it opens: the link is never up, so the
    # sender never processes a frame.
    sessions = []
    for _ in range(4):
        s = FakeSession(clock)
        s.drop()
        sessions.append(s)

    stop_event = asyncio.Event()

    async def sample_then_give_up(seconds, ev):
        clock.advance(seconds)
        boards.append(await status(state))
        if len(boards) >= 3:
            stop_event.set()
        return stop_event.is_set()

    await asyncio.wait_for(
        live_captions.sarvam_loop(
            live_microphone([speech()] * 40, clock, real_gap=0.002),
            broadcaster, False, stop_event, state,
            sarvam_cfg={"model": "saaras:v3"},
            client=FakeSarvamClient(sessions=sessions),
            retry_wait=sample_then_give_up,
        ),
        timeout=30.0,
    )

    assert boards, "the board was never sampled between connection attempts"
    loud = boards[0]["capture"]["last_loud_audio_sec_ago"]
    assert loud is not None, (
        "the microphone was delivering loud speech and the board said it had "
        "never heard anything — loudness is being measured somewhere that "
        "only runs when the speech service is reachable"
    )
    assert boards[0]["verdict"] == "capturing", boards[0]["why"]
    assert "link to the speech service is DOWN" in boards[0]["why"]
    assert "microphone is fine" in boards[0]["why"]

    # The same fault, one field along. Saying "the microphone is fine" while
    # the one number that reports HOW LOUD it is reads null invites exactly
    # the trip to the mixing desk the sentence is trying to prevent, and the
    # level was still being read only in the sender.
    level = boards[0]["capture"]["last_audio_level"]
    assert level is not None, (
        "the board told the reader the microphone was fine and then gave no "
        "level for it — the level is still being measured somewhere that "
        "only runs when the speech service is reachable"
    )
    assert level > 0, f"loud speech was captured but the level read {level}"


# ── what a surface reports about the scroll ──────────────────────────────────

async def test_the_board_keeps_the_line_boundaries_a_surface_reported():
    """The scroll's whole claim is that a WHOLE line moves at a time.

    🔴 `on_screen` joins the lines into one sentence, which is what a person
    reading the board wants — and it makes the claim uncheckable, because two
    short lines and one long line are the same string. The diagnostician
    looking at a half-line on the hall screen has to be able to see where the
    surface thinks the break is, so the report is kept BOTH ways.
    """
    app = web.Application()
    app.router.add_post("/api/overlay-report", live_captions.handle_overlay_report)
    app.router.add_get("/api/overlay-status", live_captions.handle_overlay_status)

    live_captions._overlay_reports.clear()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        posted = await client.post("/api/overlay-report", json={
            "surface": "overlay-abc123",
            "onScreen": "Bhagvan is here with us and this is the second line",
            "onScreenLines": ["Bhagvan is here with us", "and this is the second line"],
            "shown": ["an older line", "Bhagvan is here with us"],
            "state": {"queued": 1, "building": 7},
        })
        assert posted.status == 200
        body = await (await client.get("/api/overlay-status")).json()
    finally:
        await client.close()
        live_captions._overlay_reports.clear()

    surface = body["surfaces"][0]
    assert surface["on_screen_lines"] == [
        "Bhagvan is here with us", "and this is the second line",
    ]
    # Still readable as one sentence — the join is not replaced by the split.
    assert surface["on_screen"].startswith("Bhagvan is here with us and")
    assert surface["queued"] == 1


async def test_a_surface_that_reports_no_lines_does_not_break_the_board():
    """An older overlay build, or one mid-reload, posts neither field. The
    board is read during faults; it may not 500 because a browser is out of
    date."""
    app = web.Application()
    app.router.add_post("/api/overlay-report", live_captions.handle_overlay_report)
    app.router.add_get("/api/overlay-status", live_captions.handle_overlay_status)

    live_captions._overlay_reports.clear()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await client.post("/api/overlay-report", json={"surface": "old-build"})
        # And a surface that sends the wrong SHAPE, not just nothing.
        await client.post("/api/overlay-report",
                          json={"surface": "wrong-shape", "onScreenLines": "one line"})
        body = await (await client.get("/api/overlay-status")).json()
    finally:
        await client.close()
        live_captions._overlay_reports.clear()

    by_name = {s["surface"]: s for s in body["surfaces"]}
    assert by_name["old-build"]["on_screen_lines"] == []
    assert by_name["wrong-shape"]["on_screen_lines"] == []
