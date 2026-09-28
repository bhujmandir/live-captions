"""Test doubles for driving `sarvam_loop` without a network, an API key or a
microphone.

Three pieces, one for each thing the supervisor talks to:

* `FakeSarvamClient` / `FakeSession` — stand in for `AsyncSarvamAI`. Records
  every audio frame the tool sends, with the (virtual) time it was sent, and
  can emit inbound messages or drop the connection on demand. Given an
  `idle_timeout_sec` it also enforces the service's idle timeout, so a test
  about surviving a silence can actually fail.
* `FakeBroadcaster` — records every message the tool broadcasts to browser
  tabs, and doubles as the pacing signal for the scripted audio source.
* `scripted_audio` — a scripted stand-in for the microphone generator.

**Time is virtual.** `VirtualClock` is advanced one audio frame at a time by
the scripted source, so a five-minute silence costs five minutes of *audio
timeline* and a few milliseconds of wall clock. The clock is installed over
`live_captions.time` by the `clock` fixture in conftest.py.

**The scripted source is paced, not timed.** It hands over the next frame only
once the sender has demonstrably processed the previous one — observed via the
`level` message the sender broadcasts for every frame. That keeps the tool's
internal audio queue at depth <= 1, so nothing is ever dropped for backpressure
and the frame sequence the client receives is deterministic. It relies on one
invariant: frames must be at least as long as the level-broadcast cadence
(250 ms), which `FRAME_MS = 500` satisfies with room to spare.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field

import numpy as np
import websockets.exceptions as wse

# Audio frame geometry, matching what the real capture path produces.
SAMPLE_RATE = 16000
FRAME_MS = 500
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_SEC = FRAME_MS / 1000

# Amplitudes, as int16 values. The middle two sit either side of the default
# gate threshold (1% of full scale = 328) and are what pin down where the
# gate's boundary actually is; a test using only the outer two stays green
# even if the threshold moves by a factor of forty.
SPEECH_AMPLITUDE = 16384   # peak 0.500 — ordinary speech
JUST_ABOVE_GATE = 400      # peak 0.012 — quietest audio still forwarded
JUST_BELOW_GATE = 250      # peak 0.008 — loudest audio still filtered out
ROOM_AMPLITUDE = 100       # peak 0.003 — a quiet room

# How long a connection may sit with nothing sent before the fake closes it.
#
# This is the figure the tool DEFENDS AGAINST, not one the service is known to
# enforce. Measured against the live endpoint on 2026-08-31, opening a session,
# speaking briefly, then sending nothing at all:
#
#     survived 180s  ·  survived 180s  ·  survived 180s
#     survived 180s  ·  survived 150s
#     died at 50s    ·  1011 internal error — "keepalive ping timeout"
#
# So there is no reliable idle timeout at all. What does happen is a rare,
# intermittent drop, and the close reason names the real cause: the WebSocket
# protocol ping. Our client pings every 20s (the library default; the vendor
# SDK passes no ping settings), the service does not answer while idle, and
# OUR side closes the connection. The service never closed anything.
#
# The number below is kept deliberately pessimistic. The tests built on it all
# assert "the connection is never left idle for long", which is the property
# worth holding whatever the true threshold turns out to be — and over 28 hours
# of a live event a rare fault is a certainty. Do not restate 60s as a vendor
# fact anywhere; it is a defensive assumption.
# The idle timeout this harness models: a connection that has received
# nothing for this long is closed. A `FakeSession` given `idle_timeout_sec`
# enforces it, which is what makes "the session survived the silence" a claim
# a test can falsify.
#
# ⚠ This number is NOT confirmed against the live service. It comes from the
# issue that specified the keep-alive, and a live probe — two seconds of
# speech, then 150 seconds of nothing — did not reproduce it: the session
# stayed open. Treat it as the timeout we defend against rather than one we
# have observed. Tests here assert we stay comfortably inside it, which stays
# meaningful whatever the real figure turns out to be, but nothing in this
# file should be read as evidence about the vendor.
#
# Stated as its own literal rather than imported from `live_captions`: a
# double that read it from the code under test could never disagree with that
# code, which is the only way this constant ever catches anything.
SERVICE_IDLE_TIMEOUT_SEC = 60.0

# Real-clock ceiling on any wait for the tool to make progress. Only ever hit
# when something has genuinely deadlocked; keeps a broken test from hanging
# the whole run.
PROGRESS_TIMEOUT_SEC = 10.0


def frame(amplitude: int) -> bytes:
    """One frame of 16-bit mono PCM at a constant amplitude."""
    return np.full(FRAME_SAMPLES, amplitude, dtype=np.int16).tobytes()


def speech() -> bytes:
    return frame(SPEECH_AMPLITUDE)


def room() -> bytes:
    return frame(ROOM_AMPLITUDE)


def silence() -> bytes:
    """Digital silence — what a keep-alive frame carries."""
    return frame(0)


def just_above_gate() -> bytes:
    return frame(JUST_ABOVE_GATE)


def just_below_gate() -> bytes:
    return frame(JUST_BELOW_GATE)


class TooManyConnections(BaseException):
    """The tool opened more connections than the test scripted.

    Deliberately a `BaseException`. The supervisor wraps each session in a
    broad `except Exception` and retries, so an ordinary error here is
    swallowed and retried until the test's own timeout — turning an immediate,
    legible failure into a minute of waiting per test.
    """


class VirtualClock:
    """A clock the test advances by hand.

    Starts at a plausible epoch value rather than zero: the sender initialises
    its "last loud audio" marker to 0.0, so a zero-based clock would make the
    opening frames look like they arrived before any silence had elapsed.
    """

    def __init__(self, start: float = 1_700_000_000.0):
        self.now = float(start)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        """Read the clock by calling it.

        `InterruptPolicy` takes a `time.monotonic`-shaped callable rather than
        an object with a `.now`, so this fake has to be usable both ways. The
        attribute stays the one the audio-path tests already read and write.
        """
        return self.now


@dataclass
class SentFrame:
    """One audio frame as the speech service received it."""
    pcm: bytes
    at: float                # virtual clock reading when it was sent
    encoding: str
    sample_rate: int

    @property
    def peak(self) -> float:
        return float(np.abs(np.frombuffer(self.pcm, dtype=np.int16)).max()) / 32767.0


class FakeBroadcaster:
    """Stands in for `Broadcaster`. Records everything; blocks nothing."""

    def __init__(self):
        self.messages: list[dict] = []
        self._tick = asyncio.Event()

    async def send(self, msg: dict) -> None:
        self.messages.append(dict(msg))
        self._tick.set()

    # ── assertions ────────────────────────────────────────────────────────
    def types(self) -> list[str]:
        return [m.get("type") for m in self.messages]

    def of_type(self, type_: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == type_]

    @property
    def level_count(self) -> int:
        return sum(1 for m in self.messages if m.get("type") == "level")

    @property
    def connect_count(self) -> int:
        """Connections the tool has opened — it announces each one."""
        return sum(1 for m in self.messages if m.get("type") == "reconnected")

    # ── pacing ────────────────────────────────────────────────────────────
    async def wait_for_progress(self) -> None:
        """Block until the tool has dealt with the audio frame just handed to it.

        Normally that is the `level` message the sender broadcasts for every
        frame it takes off the audio queue. A frame in flight when the
        connection drops might never be processed at all, so opening a fresh
        connection counts as progress too — otherwise the script could sit
        waiting for a frame that no longer exists.
        """
        levels, connects = self.level_count, self.connect_count

        def progressed() -> bool:
            return self.level_count > levels or self.connect_count > connects

        while not progressed():
            self._tick.clear()
            if progressed():
                return
            try:
                await asyncio.wait_for(self._tick.wait(), PROGRESS_TIMEOUT_SEC)
            except asyncio.TimeoutError:  # pragma: no cover - deadlock guard
                raise AssertionError(
                    "tool stopped consuming audio after "
                    f"{self.level_count} frame(s)"
                ) from None


class FakeSession:
    """One fake speech-service WebSocket session.

    Outbound (tool -> service) frames land in `self.frames`. Inbound
    (service -> tool) traffic is whatever the test pushes with `emit()`,
    `close()` or `drop()`, delivered in order.

    `on_frame(n, session)` is awaited after every frame, where `n` is the
    1-based count of frames this session has received. That is the hook a test
    uses to make the service answer — or fail — at a chosen point in the audio.

    Given `idle_timeout_sec`, the session also enforces the real service's
    idle timeout: once nothing has arrived for that long, the connection is
    gone, and the tool discovers it the way it would in production — the next
    send raises, and the receive side sees the socket drop. Left at None the
    session tolerates any gap, which is what the tests that are not about
    silence want.
    """

    def __init__(self, clock: VirtualClock, *, on_frame=None,
                 idle_timeout_sec: float | None = None):
        self.clock = clock
        self.frames: list[SentFrame] = []
        self.flushed = False
        self.idle_timeout_sec = idle_timeout_sec
        self.idle_closed = False
        self._last_activity_at = clock.now
        self._on_frame = on_frame
        self._inbox: asyncio.Queue = asyncio.Queue()

    def opened(self) -> None:
        """Called when the tool opens the connection — the idle clock starts."""
        self._last_activity_at = self.clock.now

    # ── the surface the tool uses ─────────────────────────────────────────
    async def transcribe(self, *, audio: str, encoding: str, sample_rate: int) -> None:
        if self.idle_closed:
            raise wse.ConnectionClosedError(None, None)
        idle_for = self.clock.now - self._last_activity_at
        if self.idle_timeout_sec is not None and idle_for > self.idle_timeout_sec:
            # The service hung up `idle_for - timeout` seconds ago; this send
            # is the tool finding out.
            self.idle_closed = True
            self.drop()
            raise wse.ConnectionClosedError(None, None)
        self._last_activity_at = self.clock.now
        self.frames.append(SentFrame(
            pcm=base64.b64decode(audio),
            at=self.clock.now,
            encoding=encoding,
            sample_rate=sample_rate,
        ))
        if self._on_frame is not None:
            await self._on_frame(len(self.frames), self)

    async def flush(self) -> None:
        self.flushed = True
        # The real service ends the session once the tool has flushed.
        self.close()

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        kind, payload = await self._inbox.get()
        if kind == "message":
            return payload
        if kind == "raise":
            raise payload
        raise StopAsyncIteration

    # ── the surface the test uses ─────────────────────────────────────────
    def emit(self, msg: dict) -> None:
        """Send one message from the service to the tool."""
        self._inbox.put_nowait(("message", msg))

    def emit_transcript(self, text: str) -> None:
        self.emit({"type": "data",
                   "data": {"transcript": text,
                            "metrics": {"audio_duration": 1.0}}})

    def emit_speech_signal(self, signal: str) -> None:
        self.emit({"type": "events", "data": {"signal_type": signal}})

    def close(self) -> None:
        """Close the session cleanly."""
        self._inbox.put_nowait(("close", None))

    def drop(self) -> None:
        """Fail the session the way a genuine network fault does."""
        self._inbox.put_nowait(("raise", wse.ConnectionClosedError(None, None)))

    # ── frame views ───────────────────────────────────────────────────────
    @property
    def peaks(self) -> list[float]:
        """Peak amplitude of each frame received, 0.0–1.0."""
        return [round(f.peak, 3) for f in self.frames]

    @property
    def silent_frames(self) -> list[SentFrame]:
        """Frames carrying digital silence — what a keep-alive sends."""
        return [f for f in self.frames if f.peak == 0.0]

    @property
    def gaps(self) -> list[float]:
        """Seconds of audio between consecutive frames the service received."""
        return [b.at - a.at for a, b in zip(self.frames, self.frames[1:])]


@dataclass
class FakeSarvamClient:
    """Stands in for `AsyncSarvamAI`.

    Hands out `sessions` in order, one per connect. Connecting more times than
    there are sessions is an error — a test that reconnects unexpectedly should
    fail loudly rather than quietly carry on.
    """

    sessions: list[FakeSession]
    connect_kwargs: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.speech_to_text_streaming = _FakeStreamingApi(self)

    @property
    def connect_count(self) -> int:
        return len(self.connect_kwargs)


class _FakeStreamingApi:
    def __init__(self, client: FakeSarvamClient):
        self._client = client

    def connect(self, **kwargs):
        client = self._client
        index = len(client.connect_kwargs)
        client.connect_kwargs.append(dict(kwargs))
        if index >= len(client.sessions):
            raise TooManyConnections(
                f"tool opened connection {index + 1} but only "
                f"{len(client.sessions)} session(s) were scripted"
            )
        return _FakeConnection(client.sessions[index])


class _FakeConnection:
    """`connect(...)` returns this; the tool uses it as an async context manager."""

    def __init__(self, session: FakeSession):
        self._session = session

    async def __aenter__(self) -> FakeSession:
        self._session.opened()
        return self._session

    async def __aexit__(self, *exc_info) -> bool:
        return False


async def scripted_audio(frames, clock: VirtualClock, broadcaster: FakeBroadcaster):
    """A scripted stand-in for the microphone.

    Yields `(pcm, timestamp)` exactly like the real capture generators, one
    frame per `FRAME_SEC` of virtual time, and waits for the tool to process
    each frame before advancing the clock and handing over the next.
    """
    for index, pcm in enumerate(frames):
        if index:
            clock.advance(FRAME_SEC)
        yield pcm, clock.now
        await broadcaster.wait_for_progress()
