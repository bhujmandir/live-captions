"""A stray Ctrl+C must not take the hall's captions down.

Written against issue #61. The captions server was killed by a **console
control event** twice on the evening of 3 September — 19:19 and 22:16 — not by
a crash. Task Scheduler recorded `3221225786` (`0xC000013A`,
`STATUS_CONTROL_C_EXIT`) and the log's final bytes were literally `^C`,
immediately after healthy lines: audio `[quiet]`, `sender: sent 8`, Gemini
answering in 691 ms. The pipeline was fine. Something sent the console a
Ctrl+C and Python did as it was told.

Windows does not record who raised the event, so nothing here names a culprit.
The most likely explanation needs no malice at all: `start-captions.bat` runs
the server in a visible console window, and an operator who clicks that window
and presses Ctrl+C to *copy* a line out of it ends the katha's captions.

The properties under test are the ones that failed in the hall:

  * a single interrupt while serving is **refused**, not obeyed;
  * the refusal is *counted and reported*, so the next occurrence leaves a
    trace instead of silence;
  * a developer at a terminal can still get out — deliberately, by repeating
    it — because a server nobody can stop is its own outage;
  * a run of interrupts spread over a long period is a stray each time, not a
    developer leaving. Only a burst means "I meant it".

The design deliberately has **no detection of whether an operator is present**.
The ticket's constraint is that the check must fail toward STAYING UP, and every
available signal fails the other way: under `start-captions.bat` the server owns
a real console, so `isatty()` reports an interactive terminal and would hand back
exactly the behaviour that caused the outage. Counting the interrupts needs no
such guess.
"""

from __future__ import annotations

import asyncio

import pytest

from live_captions import (
    SHUTDOWN_HEADER,
    SHUTDOWN_HEADER_VALUE,
    InterruptPolicy,
    handle_shutdown,
)

from .fakes import VirtualClock


def test_one_interrupt_while_serving_does_not_stop_the_server():
    """The 3 September failure, as a test.

    One console control event arrives. The server must still be running
    afterwards, because in the hall this is someone copying text out of a
    window, not someone asking for the captions to end.
    """
    policy = InterruptPolicy(clock=VirtualClock())

    decision = policy.on_interrupt()

    assert decision.should_exit is False


def test_the_refusal_is_reported_so_it_leaves_a_trace():
    """#61: 'The refusal is logged, so the next occurrence leaves a trace
    instead of silence.'

    The policy does not log — it returns the line to log, so the decision can
    be tested without a logging rig. What matters is that the line names what
    happened and how to stop the server properly, because the person reading it
    is looking at a console mid-katha.
    """
    policy = InterruptPolicy(clock=VirtualClock())

    decision = policy.on_interrupt()

    assert decision.message
    assert "ignored" in decision.message.lower()
    assert "stop-captions" in decision.message.lower()


def test_a_burst_of_interrupts_lets_a_developer_out():
    """A server nobody can stop is its own outage.

    Three interrupts inside the window is a person meaning it. The current
    behaviour — Ctrl+C at a developer terminal exits cleanly — survives, it
    just costs three presses instead of one.
    """
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=3, escape_window_sec=2.0)

    assert policy.on_interrupt().should_exit is False
    clock.advance(0.3)
    assert policy.on_interrupt().should_exit is False
    clock.advance(0.3)
    assert policy.on_interrupt().should_exit is True


def test_interrupts_spread_over_time_never_add_up_to_an_exit():
    """The failure mode this exists to prevent.

    An operator who copies a line out of the console once an hour, all evening,
    must never accumulate their way into stopping the captions. Only a burst
    counts, so the count is of interrupts *inside the window*, not of
    interrupts ever seen.
    """
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=3, escape_window_sec=2.0)

    for _ in range(10):
        assert policy.on_interrupt().should_exit is False
        clock.advance(3600.0)


def test_the_window_is_measured_from_the_presses_that_are_still_counting():
    """Two presses, a long gap, then two more is not three in a row.

    The first pair has expired by the time the second pair arrives, so the
    count starts again from one. It takes a fresh burst of three — the earlier
    pair buys the second burst nothing.
    """
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=3, escape_window_sec=2.0)

    policy.on_interrupt()
    clock.advance(0.5)
    policy.on_interrupt()

    clock.advance(60.0)

    assert policy.on_interrupt().should_exit is False
    clock.advance(0.5)
    assert policy.on_interrupt().should_exit is False
    clock.advance(0.5)
    assert policy.on_interrupt().should_exit is True


def test_the_escape_hatch_can_be_switched_off_entirely():
    """A control-room machine can refuse every interrupt.

    `escape_presses=0` means there is no key combination that ends the katha's
    captions. The documented stop path is still `stop-captions`, which is how
    #61 requires it: not unkillable, but not killable by accident either.
    """
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=0)

    for _ in range(20):
        assert policy.on_interrupt().should_exit is False
        clock.advance(0.1)


def test_the_message_counts_the_presses_left():
    """Someone who does mean it should not have to guess.

    The refusal says how many more presses would exit, so a developer is told
    the way out rather than reaching for Task Manager.
    """
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=3, escape_window_sec=2.0)

    first = policy.on_interrupt()
    clock.advance(0.1)
    second = policy.on_interrupt()

    assert "2 more" in first.message
    assert "1 more" in second.message


@pytest.mark.parametrize("presses", [1, 2, 5])
def test_the_number_of_presses_is_configurable(presses: int):
    """The mandir PC and a laptop want different numbers."""
    clock = VirtualClock()
    policy = InterruptPolicy(clock=clock, escape_presses=presses, escape_window_sec=2.0)

    for _ in range(presses - 1):
        assert policy.on_interrupt().should_exit is False
        clock.advance(0.1)

    assert policy.on_interrupt().should_exit is True


# ── The other half of #61: a documented way to stop that is not Task Manager ──


class StubRequest:
    """Enough of an aiohttp request for the shutdown handler."""

    def __init__(self, remote: str, app: dict, headers: dict | None = None):
        self.remote = remote
        self.app = app
        self.headers = headers if headers is not None else {}


def _control_headers() -> dict:
    return {SHUTDOWN_HEADER: SHUTDOWN_HEADER_VALUE}


async def _call_shutdown(remote: str, app: dict, headers: dict | None = None):
    return await handle_shutdown(StubRequest(remote, app, headers))


async def test_shutdown_from_the_machine_itself_stops_the_server():
    """Refusing Ctrl+C is only safe if something else ends the process.

    #61: 'There is a documented, non-Task-Manager way to end it.' This is it,
    and it ends the server through the ordinary teardown rather than killing it.

    The event is set on a later turn of the loop so the response can be written
    first — otherwise the teardown races the reply and the stop script reports
    a failure for a shutdown that worked. So this waits for it rather than
    asserting immediately.
    """
    event = asyncio.Event()
    response = await _call_shutdown("127.0.0.1", {"shutdown_event": event},
                                    _control_headers())

    assert response.status == 200
    await asyncio.wait_for(event.wait(), timeout=2.0)


async def test_shutdown_is_refused_from_anywhere_but_the_machine_itself():
    """The server listens on every interface so vMix on another box can reach
    the overlay. Ending the whole process is a bigger hammer than `/api/stop`
    and exists for a script running locally, so the network is not offered it.
    """
    event = asyncio.Event()
    response = await _call_shutdown("192.168.1.50", {"shutdown_event": event},
                                    _control_headers())

    assert response.status == 403
    assert not event.is_set()


async def test_a_web_page_open_on_the_mandir_pc_cannot_stop_the_captions():
    """The hole a localhost check alone leaves open, and the reason for the header.

    The operator's own browser IS 127.0.0.1. Any page open on that machine can
    auto-submit a form-encoded POST to this URL — a 'simple request', so no
    preflight, no consent and no visible sign — and the localhost check would
    wave it straight through. A cross-origin form cannot set a custom header,
    which is the whole point of requiring one.

    This is the shape of the original fault, one layer up: something ends the
    katha's captions and nobody can tell what did it.
    """
    event = asyncio.Event()

    response = await _call_shutdown("127.0.0.1", {"shutdown_event": event})

    assert response.status == 403
    assert not event.is_set()


async def test_the_wrong_header_value_is_refused_too():
    """Present-but-wrong must fail the same way as absent."""
    event = asyncio.Event()

    response = await _call_shutdown(
        "127.0.0.1", {"shutdown_event": event}, {SHUTDOWN_HEADER: "please"}
    )

    assert response.status == 403
    assert not event.is_set()


async def test_shutdown_reports_a_server_that_has_no_event_rather_than_crashing():
    """A 500 with a reason beats a traceback into a console nobody is reading."""
    response = await _call_shutdown("127.0.0.1", {}, _control_headers())

    assert response.status == 500
