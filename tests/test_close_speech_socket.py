"""`_close_speech_socket` against the shape the REAL SDK has.

`FakeSession` answers `close()` itself, which is convenient but shadows the
branch that matters in production: `AsyncSpeechToTextStreamingSocketClient`
has no `close` at all, only a private `_websocket`. Every other test in this
suite exercises the first branch and would stay green through a typo in the
fallback. These drive the fallback directly.
"""

from __future__ import annotations

import live_captions


class _RealShapedSession:
    """No `close()` — exactly like the SDK's session object."""

    def __init__(self, inner):
        self._websocket = inner


class _Socket:
    def __init__(self, raises: BaseException | None = None):
        self.closed = False
        self._raises = raises

    async def close(self):
        if self._raises is not None:
            raise self._raises
        self.closed = True


async def test_it_closes_the_websocket_under_a_session_that_has_no_close():
    inner = _Socket()
    await live_captions._close_speech_socket(_RealShapedSession(inner), "test")
    assert inner.closed, "the fallback to _websocket.close() did not fire"


async def test_a_session_close_that_raises_still_reaches_the_socket_underneath():
    """A `close()` that raises has most likely left the socket open.

    Returning at that point would leave the reader parked with no second
    attempt — the one thing this function exists to prevent.
    """
    inner = _Socket()

    class _Wedged(_RealShapedSession):
        def close(self):
            raise RuntimeError("SDK close is broken")

    await live_captions._close_speech_socket(_Wedged(inner), "test")
    assert inner.closed, "a raising close() stopped the fallback from running"


async def test_nothing_to_close_is_survivable(caplog):
    """No `close` anywhere. It must say so, not raise."""
    class _Opaque:
        pass

    await live_captions._close_speech_socket(_Opaque(), "test")
    assert "nothing left to call" in caplog.text


async def test_a_socket_close_that_raises_is_reported_not_raised():
    """The last resort failing must not become the caller's exception —
    this runs inside a session teardown that has to complete."""
    session = _RealShapedSession(_Socket(raises=RuntimeError("socket gone")))
    await live_captions._close_speech_socket(session, "test")  # must not raise
