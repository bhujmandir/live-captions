"""Test doubles for the connection state and the reconnect supervisor.

`tests/fakes.py` covers the audio path — a scripted microphone, a fake speech
service and a virtual clock advanced one frame at a time. These add the four
things a test about the *connection* needs on top of it:

* `idle_audio` — a microphone that stays open and hands over nothing, so the
  only thing that can move the clock is the supervisor's own wait.
* `InstantRetryWait` — stands in for the supervisor's hold between connection
  attempts. That hold is the one wait in `sarvam_loop` that runs on the event
  loop's clock rather than on `time.time()`, so the virtual clock cannot reach
  it; substituting it is what makes an assertion about attempt *spacing* cost
  milliseconds instead of seconds.
* `TimedSarvamClient` — a `FakeSarvamClient` that also notes the virtual time
  of every connect, which is the only thing spacing is observable as.
* `TabFanout` — a `FakeBroadcaster` that also pushes everything through the
  real `Broadcaster`, so one test can drive the supervisor and still assert on
  what genuinely lands on a browser tab's WebSocket.
"""

from __future__ import annotations

import asyncio

from .fakes import FakeBroadcaster, FakeSarvamClient, FakeSession, VirtualClock


async def idle_audio(stop_event: asyncio.Event):
    """A microphone that is open, and silent, for the whole session.

    The supervisor keeps reconnecting while a source is alive, so a test of
    reconnect timing needs one that never ends — and never advances the audio
    timeline either, otherwise it would be sharing the clock with the thing
    under test.
    """
    await stop_event.wait()
    for pcm in ():          # never taken; this is what makes it a generator
        yield pcm, 0.0


class InstantRetryWait:
    """Stands in for `sarvam_loop`'s wait between connection attempts.

    Records what was asked for, advances the virtual clock by exactly that,
    and returns at once. `stop_after` ends the session on that many waits —
    i.e. after that many attempts — which is how a test bounds a supervisor
    that would otherwise reconnect for as long as the audio source lives.
    """

    def __init__(self, clock: VirtualClock, *, stop_after: int | None = None):
        self.clock = clock
        self.delays: list[float] = []
        self._stop_after = stop_after

    async def __call__(self, seconds: float, stop_event: asyncio.Event) -> bool:
        self.delays.append(seconds)
        self.clock.advance(seconds)
        if self._stop_after is not None and len(self.delays) >= self._stop_after:
            stop_event.set()
        return stop_event.is_set()


class TimedSarvamClient:
    """A `FakeSarvamClient` that also notes the virtual time of every connect.

    How far apart the supervisor spaces its attempts is not visible in any
    message it sends; from outside, it is the interval between one connection
    and the next. `gaps` is that.
    """

    def __init__(self, clock: VirtualClock, sessions: list[FakeSession]):
        self._inner = FakeSarvamClient(sessions=sessions)
        self._clock = clock
        self.connect_at: list[float] = []
        self.speech_to_text_streaming = _TimedStreamingApi(self)

    @property
    def connect_kwargs(self) -> list[dict]:
        return self._inner.connect_kwargs

    @property
    def connect_count(self) -> int:
        return len(self.connect_at)

    @property
    def gaps(self) -> list[float]:
        """Seconds between one connection attempt and the next."""
        return [round(b - a, 3) for a, b in zip(self.connect_at, self.connect_at[1:])]


class _TimedStreamingApi:
    def __init__(self, client: TimedSarvamClient):
        self._client = client

    def connect(self, **kwargs):
        self._client.connect_at.append(self._client._clock.now)
        return self._client._inner.speech_to_text_streaming.connect(**kwargs)


class TabFanout(FakeBroadcaster):
    """A `FakeBroadcaster` that also fans out through a real `Broadcaster`.

    Driving the supervisor needs the fake — the scripted audio source paces
    itself on it. Proving a message reached a browser tab needs the real one.
    This is both, so a single test can do both.
    """

    def __init__(self, real):
        super().__init__()
        self.real = real

    async def send(self, msg: dict) -> None:
        await super().send(msg)
        await self.real.send(msg)
