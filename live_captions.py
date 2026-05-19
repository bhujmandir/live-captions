#!/usr/bin/env python3
"""
Live captions server — Sarvam-backed streaming STT + translation.

Open http://localhost:8765 in a browser on the host Mac.
Select the audio source (mic / input device / test file), pick a language
direction (gu→en or en→gu), and click Start. Captions appear in real time,
styled for display in ProPresenter, OBS, or any browser overlay.

Usage:
    uv run python live_captions.py              # starts server, open browser
    uv run python live_captions.py --port 9000  # different port
    uv run python live_captions.py --propresenter  # also push to PP messages overlay
"""

import argparse
import asyncio
import base64
import collections
import json
import logging
import os
import re
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("captions")

# ── Ring buffer for the in-browser debug panel ───────────────────────────────
# A logging.Handler appends pipeline-relevant records into a bounded deque
# AND fans them out over the WS bus so the React debug panel renders in
# real time. New WS clients get the current ring as a one-shot snapshot
# on connect (see handle_ws), then receive live `log` events. Only signal
# goes in — HTTP access logs and other framework noise are excluded so
# the panel stays focused on the audio/STT pipeline.
_recent_logs: collections.deque = collections.deque(maxlen=400)

# Populated from main() once the event loop and Broadcaster exist. The
# logging handler is a sync callable invoked from any thread; these two
# refs let it schedule a broadcast back onto the server's loop without
# blocking the caller.
_log_loop:        "asyncio.AbstractEventLoop | None" = None
_log_broadcaster: "Broadcaster | None" = None

_BLOCKED_LOGGERS = {
    "aiohttp.access",
    "aiohttp.server",
    "aiohttp.web",
    "aiohttp.websocket",
    "asyncio",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "urllib3",
}

class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name in _BLOCKED_LOGGERS:
            return
        try:
            entry = {
                "t":      record.created,
                "level":  record.levelname,
                "logger": record.name,
                "msg":    record.getMessage(),
            }
            _recent_logs.append(entry)
        except Exception:
            return
        loop, caster = _log_loop, _log_broadcaster
        if loop is None or caster is None:
            return
        # Schedule the broadcast on the loop's thread. We can't await here
        # — emit() is sync, may be called from sounddevice's callback
        # thread or any logger caller. ensure_future inside the
        # threadsafe callback is the standard sync→async bridge.
        try:
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    caster.send({"type": "log", "record": entry}), loop=loop))
        except RuntimeError:
            pass  # loop already closed during shutdown

_ring = _RingHandler()
_ring.setLevel(logging.INFO)
logging.getLogger().addHandler(_ring)

PP_HOST = os.environ.get("PP_HOST", "127.0.0.1")
PP_PORT = int(os.environ.get("PP_PORT", "49566"))
PP_MESSAGE_NAME = "Live Caption"

# Per-deployment branding. Set in .env; defaults are intentionally neutral so
# out-of-the-box there's no event-specific branding. APP_NAME shows in the
# browser title and operator header; ACCENT_COLOR drives the highlight used
# for buttons and status pills.
APP_NAME     = os.environ.get("APP_NAME",     "Live Captions").strip() or "Live Captions"
ACCENT_COLOR = os.environ.get("ACCENT_COLOR", "#FF8C00").strip() or "#FF8C00"


def _hex_to_hsl_channels(color: str) -> str:
    """Convert `#RRGGBB` (or `RRGGBB`) to space-separated HSL channels
    suitable for a Tailwind theme variable — e.g. `#FF8C00` → `33 100% 50%`.
    Tailwind opacity modifiers like `bg-accent/30` require the channel form
    inside `hsl(var(--accent) / <alpha>)`, so a raw hex won't compose."""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = (int(c[i:i+2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        r, g, b = 1.0, 0.55, 0.0   # orange fallback matches default ACCENT_COLOR
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if   mx == r: h = ((g - b) / d + (6 if g < b else 0)) / 6
        elif mx == g: h = ((b - r) / d + 2) / 6
        else:         h = ((r - g) / d + 4) / 6
    return f"{round(h * 360)} {round(s * 100)}% {round(l * 100)}%"


ACCENT_HSL = _hex_to_hsl_channels(ACCENT_COLOR)

# ── Language matrix ──────────────────────────────────────────────────────────
# Every source language Sarvam Saaras v3 accepts (per its SDK Literal), plus
# the native-script name shown to the operator. Used by the JS dropdowns and
# the server-side mode/model derivation. Codes are exactly what Sarvam expects
# (e.g. od-IN for Odia, not or-IN). Mayura/sarvam-translate uses the same
# codes for target_language_code.
SARVAM_LANGS: list[tuple[str, str]] = [
    ("en-IN",  "English"),
    ("hi-IN",  "Hindi  हिन्दी"),
    ("bn-IN",  "Bengali  বাংলা"),
    ("gu-IN",  "Gujarati  ગુજરાતી"),
    ("kn-IN",  "Kannada  ಕನ್ನಡ"),
    ("ml-IN",  "Malayalam  മലയാളം"),
    ("mr-IN",  "Marathi  मराठी"),
    ("od-IN",  "Odia  ଓଡ଼ିଆ"),
    ("pa-IN",  "Punjabi  ਪੰਜਾਬੀ"),
    ("ta-IN",  "Tamil  தமிழ்"),
    ("te-IN",  "Telugu  తెలుగు"),
    ("as-IN",  "Assamese  অসমীয়া"),
    ("ur-IN",  "Urdu  اردو"),
    ("ne-IN",  "Nepali  नेपाली"),
    ("kok-IN", "Konkani  कोंकणी"),
    ("ks-IN",  "Kashmiri  कॉशुर"),
    ("sd-IN",  "Sindhi  सिन्धी"),
    ("sa-IN",  "Sanskrit  संस्कृतम्"),
    ("sat-IN", "Santali  ᱥᱟᱱᱛᱟᱲᱤ"),
    ("mni-IN", "Manipuri  মৈতৈ"),
    ("brx-IN", "Bodo  बर'"),
    ("mai-IN", "Maithili  मैथिली"),
    ("doi-IN", "Dogri  डोगरी"),
]
SARVAM_LANG_CODES: set[str] = {code for code, _ in SARVAM_LANGS}
_SARVAM_LANG_NAME: dict[str, str] = {code: name for code, name in SARVAM_LANGS}

def langname(code: str) -> str:
    """Display name for a Sarvam lang code — falls back to the code itself
    if it's unknown so labels never crash. Used for seeding default feeds
    and any future log-friendly rendering on the server side."""
    raw = _SARVAM_LANG_NAME.get(code, code)
    # Drop the native-script tail for clean log lines: "Gujarati  ગુજરાતી" → "Gujarati"
    return raw.split("  ")[0] if "  " in raw else raw

# Per-deployment direction defaults — set in .env so a fresh deploy lands on
# the org's most-common direction without operator setup.
DEFAULT_SOURCE_LANG = os.environ.get("DEFAULT_SOURCE_LANG", "en-IN").strip() or "en-IN"
DEFAULT_TARGET_LANG = os.environ.get("DEFAULT_TARGET_LANG", "en-IN").strip() or "en-IN"
if DEFAULT_SOURCE_LANG not in SARVAM_LANG_CODES:
    DEFAULT_SOURCE_LANG = "en-IN"
if DEFAULT_TARGET_LANG not in SARVAM_LANG_CODES:
    DEFAULT_TARGET_LANG = "en-IN"

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"


def derive_pipeline(source: str, target: str) -> dict:
    """Given a (source, target) lang pair, decide the Sarvam pipeline:

    * `source == target`  →  Saaras transcribe (no Mayura)
    * `target == en-IN` and source is Indic  →  Saaras translate-mode (1 call)
    * everything else  →  Saaras transcribe in source + Mayura source→target

    Returns:
      {
        "sarvam_mode":       "translate" | "transcribe",
        "sarvam_lang":       <source>,
        "saaras_output_lang":<lang the Saaras transcript will be in>,
        "needs_mayura":      bool,
      }
    """
    if source == target:
        return {"sarvam_mode": "transcribe", "sarvam_lang": source,
                "saaras_output_lang": source, "needs_mayura": False}
    if target == "en-IN" and source != "en-IN":
        return {"sarvam_mode": "translate", "sarvam_lang": source,
                "saaras_output_lang": "en-IN", "needs_mayura": False}
    return {"sarvam_mode": "transcribe", "sarvam_lang": source,
            "saaras_output_lang": source, "needs_mayura": True}


# Mayura (default model) covers EN ↔ 10 Indic. sarvam-translate covers the
# full 22 Indic set. Pick the wider model when either side falls outside
# Mayura's set so exotic pairs (e.g. Sanskrit ↔ Manipuri) still translate.
MAYURA_LANGS: frozenset[str] = frozenset({
    "en-IN", "hi-IN", "bn-IN", "gu-IN", "ta-IN", "te-IN",
    "kn-IN", "ml-IN", "mr-IN", "pa-IN", "od-IN",
})

def mayura_model_for(source: str, target: str) -> str | None:
    if source in MAYURA_LANGS and target in MAYURA_LANGS:
        return None  # Sarvam's default = Mayura, best quality for these
    return "sarvam-translate"


def yt_lang_from_sarvam(code: str) -> str:
    """YouTube CC's `lang=` URL param accepts BCP-47; the primary subtag
    (en, gu, hi, …) is most broadly recognised on YouTube. Strip the
    region from "en-IN" → "en"."""
    return (code or "en").split("-")[0] or "en"


# ── Device listing ────────────────────────────────────────────────────────────

def list_audio_devices() -> list[dict]:
    import sounddevice as sd
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            devices.append({
                "id": str(i),
                "name": d["name"],
                "channels": d["max_input_channels"],
            })
    return devices


# ── Audio generators ──────────────────────────────────────────────────────────

async def audio_from_file(path: str, max_seconds: float | None, chunk_ms: int = 500):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd

    data, sr = sf.read(path, dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)
    if max_seconds:
        data = data[: int(sr * max_seconds)]
    if sr != 16000:
        g = gcd(sr, 16000)
        data = resample_poly(data.astype("float32"), 16000 // g, sr // g)
        data = data.clip(-32768, 32767).astype(np.int16)
        sr = 16000
    samples = int(sr * chunk_ms / 1000)
    t0 = time.time()
    for i in range(0, len(data), samples):
        yield data[i : i + samples].tobytes(), time.time()
        nxt = t0 + (i + samples) / sr
        await asyncio.sleep(max(0.0, nxt - time.time()))


async def audio_from_device(device_id: str | None, chunk_ms: int = 500):
    import numpy as np
    import sounddevice as sd

    dev = int(device_id) if device_id and device_id.isdigit() else device_id
    sr, n = 16000, int(16000 * chunk_ms / 1000)
    # maxsize 8 × 500 ms = 4 s buffer. Big enough to ride out brief network
    # hiccups, small enough that recovery doesn't introduce permanent caption
    # lag. On overflow we drop the OLDEST chunk — stale audio is useless for
    # STT, the freshest matters most.
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()
    drops = {"count": 0, "last_warn": 0.0}
    # Audio-level stats updated from the PortAudio thread, read from the
    # asyncio loop. Lets the operator see at a glance whether the mic is
    # actually producing sound (peak ~0 = silent / wrong device / muted).
    stats = {"peak": 0.0, "rms_sum": 0.0, "n": 0}

    def _enqueue(item):
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                return
            drops["count"] += 1
            now = time.time()
            if now - drops["last_warn"] > 5:
                log.warning(
                    f"Audio queue full — dropped {drops['count']} chunks "
                    "(Sarvam/network can't keep up). Captions may stall."
                )
                drops["last_warn"] = now

    def cb(indata, frames, t, status):
        samples = indata[:, 0]
        peak = float(abs(samples).max())
        rms  = float(np.sqrt((samples * samples).mean()))
        if peak > stats["peak"]:
            stats["peak"] = peak
        stats["rms_sum"] += rms
        stats["n"] += 1
        pcm = (samples * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        loop.call_soon_threadsafe(_enqueue, (pcm, time.time()))

    # Separate task so the level log fires even when the consumer (sender →
    # ws.transcribe) is hung. If the generator's body owned the log, a stuck
    # sender would silence the panel and we'd be blind.
    async def _level_logger():
        try:
            while True:
                await asyncio.sleep(2.0)
                n_blocks = stats["n"]
                if n_blocks == 0:
                    log.warning("audio level: NO callbacks from PortAudio in last 2s "
                                "(mic permission denied? device disconnected?)")
                    continue
                avg_rms  = stats["rms_sum"] / n_blocks
                peak_pct = stats["peak"] * 100
                rms_pct  = avg_rms * 100
                # <1% peak  → effectively silent (wrong device / muted / mic perm)
                # 1-5%      → background hum only
                # 5-70%     → speech in the room
                # >70%      → very loud / clipping risk
                tag = ("SILENT" if peak_pct < 1 else
                       "quiet"  if peak_pct < 5 else
                       "ok"     if peak_pct < 70 else
                       "LOUD")
                log.info(f"audio level: peak={peak_pct:4.1f}% rms={rms_pct:4.1f}% [{tag}] "
                         f"({n_blocks} chunks)")
                stats["peak"] = 0.0
                stats["rms_sum"] = 0.0
                stats["n"] = 0
        except asyncio.CancelledError:
            return

    log.info(f"audio: opening device={dev!r} sr={sr} chunk={chunk_ms}ms")
    level_task = asyncio.create_task(_level_logger())
    try:
        with sd.InputStream(device=dev, samplerate=sr, channels=1,
                            blocksize=n, dtype="float32", callback=cb):
            log.info("audio: stream open — capturing")
            while True:
                yield await q.get()
    finally:
        level_task.cancel()
        try:
            await level_task
        except Exception:
            pass


# ── Audio level monitor (runs when not transcribing) ─────────────────────────

async def audio_monitor_loop(device_id, broadcaster: "Broadcaster", stop_event: asyncio.Event):
    """Capture from the given input device and broadcast peak level events
    every 250 ms. Lets the operator see if the mic is hot before pressing
    Start. Releases the device the moment stop_event is set (called by
    handle_start before kicking off a real transcription session, since
    CoreAudio gives exclusive access and Sarvam's sender needs the device).
    """
    import sounddevice as sd
    import numpy as np

    dev = int(device_id) if device_id and str(device_id).isdigit() else device_id
    sr = 16000
    blocksize = int(sr * 0.05)   # 50 ms callback cadence

    state = {"peak": 0.0}
    def cb(indata, frames, t, status):
        try:
            samples = indata[:, 0]
            p = float(np.abs(samples).max())
            if p > state["peak"]:
                state["peak"] = p
        except Exception:
            pass

    try:
        log.info(f"monitor: opening device={dev!r} sr={sr}")
        with sd.InputStream(device=dev, samplerate=sr, channels=1,
                            blocksize=blocksize, dtype="float32", callback=cb):
            log.info("monitor: streaming level events")
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.25)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    await broadcaster.send({"type": "level", "peak": state["peak"]})
                except Exception:
                    pass
                state["peak"] = 0.0
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # Common case: device already open by another process, wrong index,
        # or mic permission denied. Surface to the panel so the operator
        # knows the meter isn't going to update.
        log.warning(f"monitor: failed to open device={dev!r}: {e!r}")
        try:
            await broadcaster.send({"type": "level", "peak": 0.0})
        except Exception:
            pass
    finally:
        log.info("monitor: stopped")


# ── ProPresenter ─────────────────────────────────────────────────────────────

async def get_or_create_pp_message(session: aiohttp.ClientSession) -> str | None:
    base = f"http://{PP_HOST}:{PP_PORT}"
    try:
        async with session.get(f"{base}/v1/messages") as r:
            if r.status == 200:
                for msg in (await r.json()).get("messages", []):
                    if msg.get("name") == PP_MESSAGE_NAME:
                        return msg["id"]
        payload = {"name": PP_MESSAGE_NAME, "tokens": [{"name": "text", "text": {"text": "", "size": 48}}]}
        async with session.post(f"{base}/v1/messages", json=payload) as r:
            if r.status in (200, 201):
                return (await r.json()).get("id")
    except Exception as e:
        log.warning(f"ProPresenter: {e}")
    return None


async def push_to_pp(session: aiohttp.ClientSession, msg_id: str, text: str):
    base = f"http://{PP_HOST}:{PP_PORT}"
    try:
        async with session.put(f"{base}/v1/messages/{msg_id}",
                               json={"tokens": [{"name": "text", "text": {"text": text}}]}) as _: pass
        async with session.put(f"{base}/v1/messages/{msg_id}/trigger") as _: pass
    except Exception as e:
        log.debug(f"PP push: {e}")


# ── Broadcaster ───────────────────────────────────────────────────────────────

class Broadcaster:
    def __init__(self):
        self._clients: set[web.WebSocketResponse] = set()

    def add(self, ws):    self._clients.add(ws)
    def remove(self, ws): self._clients.discard(ws)

    async def send(self, msg: dict):
        payload = json.dumps(msg)
        if not self._clients:
            return
        # Send to all clients concurrently with a per-client timeout. A single
        # slow / throttled browser tab (Chrome aggressively throttles background
        # tabs, which can stall a sequential send loop here and back-pressure
        # everything upstream including the Sarvam receive loop) must not
        # affect the others or the upstream STT pipeline.
        async def _one(ws):
            try:
                await asyncio.wait_for(ws.send_str(payload), timeout=0.5)
                return ws, None
            except Exception as e:
                return ws, e
        results = await asyncio.gather(*(_one(c) for c in list(self._clients)))
        for ws, err in results:
            if err is not None:
                self._clients.discard(ws)


# ── YouTube live captions (POST captions to a URL) ────────────────────────────
#
# YouTube's legacy live-caption ingest endpoint, used by OBS / vMix / StreamText
# and friends. URL pattern, body format and behaviour are documented at:
#   https://support.google.com/youtube/answer/3068031  (operator setup only)
#   https://github.com/theowoo/webcaptioner-youtube/blob/master/stream.py
#   https://stackoverflow.com/questions/66143575  (reverse-engineered details)
#
#   POST http://upload.youtube.com/closedcaption?cid=<STREAM_KEY>&seq=<N>&lang=en
#   Content-Type: text/plain
#   Body: "<ISO 8601 UTC timestamp>\n<caption text>\n"
#
# `seq` monotonically increases per session. `cid` is the persistent stream key
# (NOT a per-broadcast id) — same key used in the marquee ProPresenter / Wowza
# RTMP push. The broadcast must have "Closed captions" enabled in YouTube Studio
# with captioning method "POST captions to URL".
#
# Operator-facing semantics of `delay_sec` (= "Caption advance" in the UI):
#   The POST body's wall-clock timestamp is set to
#       body_ts = captured_at - delay_sec
#   where captured_at = wall-clock time we received the FINAL from Sarvam.
#   YouTube anchors the caption to the video frame whose CAPTURE time matches
#   body_ts. Increasing delay_sec anchors the caption EARLIER in the video
#   timeline, which means it appears EARLIER on viewer screens.
#
#   Default ~1.5 s compensates for Sarvam's typical FINAL latency (audio is
#   buffered + VAD + STT processing), so captions land roughly in sync with the
#   spoken words. Increase if captions still feel late on the viewer, decrease
#   (toward 0) if they appear before the words are spoken.
#
#   IMPORTANT: this is NOT a queue-hold timer — there is no "wait N seconds then
#   send". POSTs go out as soon as Sarvam returns; only the body_ts is offset.
#   YouTube buffers ingested captions internally and applies them when the
#   matching video frame plays, so out-of-order or "future" stream delivery is
#   handled by YouTube, not by us.
#
#   ⚠ Naming asymmetry with the Pi sidecar:
#     - YouTube `delay_sec` (here): positive ⇒ caption appears EARLIER on viewer
#       (subtracted from body_ts, anchor moves into the past).
#     - Pi `CAPTIONS_DELAY_SEC` (streaming/pi/captions-sidecar.py): positive ⇒
#       caption appears LATER on screen (queue hold before file write).
#   The two paths control different surfaces (timeline anchor vs render time),
#   so the directions inevitably differ. TODO(post-event): revisit unifying
#   the naming or surfacing both via a single "timeline offset" abstraction.
import datetime as _dt

YT_CC_URL                = "http://upload.youtube.com/closedcaption"
YT_CC_LANG               = "en"
YT_CC_SILENCE_CLEAR_SEC  = 10.0   # blank the on-screen caption after this much silence
YT_CC_QUEUE_MAX          = 64
YT_CC_HTTP_TIMEOUT_SEC   = 5.0


class YouTubeCaptionPusher:
    """Background worker that POSTs live captions to YouTube's CC ingest URL.

    One instance per process, started in main(). FINALs from sarvam_loop are
    `submit()`-ed; the worker drains the internal queue, waits out the
    operator-tuned delay, and POSTs to YouTube. Disabled by default — operator
    flips it on per session via the UI toggle. Failures are logged but never
    kill the worker — that keeps the LED-wall caption path safe even if YT is
    flaky.
    """

    def __init__(self, stream_key: str, broadcaster: "Broadcaster"):
        self.stream_key  = stream_key
        self.broadcaster = broadcaster
        self.enabled     = False
        # YouTube CC language tag, sent as ?lang=… on every POST. Defaults to
        # English; the direction-flip handler calls set_lang() when the
        # operator picks en_gu (target=gu) and back to "en" for gu_en.
        self.lang        = YT_CC_LANG
        # Default 1.5 s ≈ typical Sarvam FINAL latency. See the operator-
        # semantics block above the class for what this number means.
        self.delay_sec   = 1.5
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=YT_CC_QUEUE_MAX)
        self._seq         = 0
        self._sent        = 0
        self._errors      = 0
        self._last_sent_at         = 0.0
        self._last_error_msg       = ""
        self._last_status_at       = 0.0
        self._session: aiohttp.ClientSession | None = None
        self._stop = asyncio.Event()

    @property
    def configured(self) -> bool:
        return bool(self.stream_key)

    def set_lang(self, lang: str) -> None:
        """Update the YouTube CC `lang=` URL param. Called by the direction
        flip handler so a mid-session swap to en_gu (target=gu) lands
        captions on YouTube's Gujarati track, not English. Empty / invalid
        values are ignored — keep whatever was last set."""
        lang = (lang or "").strip().lower()
        if not lang or lang == self.lang:
            return
        log.info(f"YouTube CC: lang {self.lang!r} → {lang!r}")
        self.lang = lang
        # Force a status broadcast next tick so the operator UI reflects
        # the language change (we don't expose lang in status() yet, but
        # this ensures any future surfacing isn't stale).
        self._last_status_at = 0.0

    def configure(self, *, enabled: bool | None = None, delay_sec: float | None = None,
                  stream_key: str | None = None) -> None:
        # Stream key updates land BEFORE the enable check so an operator can
        # paste a key + tick the box in a single POST.
        if stream_key is not None:
            new_key = (stream_key or "").strip()
            if new_key and new_key != self.stream_key:
                tail = new_key[-4:] if len(new_key) >= 4 else "?"
                log.info(f"YouTube CC: stream key updated (…{tail})")
                # On key change, reset session-scoped counters — `seq` must be
                # monotonic per (key, broadcast), so a new key gets a fresh seq.
                self.stream_key = new_key
                self._seq = 0
                self._sent = 0
                self._errors = 0
                self._last_error_msg = ""
        if enabled is not None:
            was = self.enabled
            self.enabled = bool(enabled) and self.configured
            if not was and self.enabled:
                log.info(f"YouTube CC: ENABLED (advance {self.delay_sec:.1f}s)")
            elif was and not self.enabled:
                log.info("YouTube CC: disabled — draining queue")
                # Drain pending captions so flipping back on doesn't replay stale text.
                drained = 0
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                        drained += 1
                    except asyncio.QueueEmpty:
                        break
                if drained:
                    log.info(f"YouTube CC: dropped {drained} pending captions on disable")
        if delay_sec is not None:
            self.delay_sec = max(0.0, float(delay_sec))

    def submit(self, text: str, captured_at: float | None = None) -> None:
        """Enqueue a FINAL for delayed POST. Silent no-op when disabled."""
        if not self.enabled or not text:
            return
        if captured_at is None:
            captured_at = time.time()
        try:
            self._queue.put_nowait((text, captured_at))
        except asyncio.QueueFull:
            # Backpressure indicator: YT POSTs are slower than the speaker.
            # Drop the OLDEST entry to keep recent captions flowing — stale
            # captions help no one.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((text, captured_at))
                log.warning("YouTube CC: queue full, dropped oldest")
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def status(self) -> dict:
        # Expose only the last 4 chars of the stream key — it's a credential
        # and any tab that opens /ws receives this snapshot.
        tail = self.stream_key[-4:] if len(self.stream_key) >= 4 else ""
        return {
            "configured":      self.configured,
            "enabled":         self.enabled,
            "delay_sec":       self.delay_sec,
            "stream_key_tail": tail,
            "sent":            self._sent,
            "errors":          self._errors,
            "seq":             self._seq,
            "queue_size":      self._queue.qsize(),
            "last_sent_at":    self._last_sent_at,
            "last_error":      self._last_error_msg,
        }

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        # The worker is started unconditionally at process boot so an operator
        # who sets the key via the UI mid-session doesn't need a restart. The
        # configured/enabled gates in submit() and at dequeue time keep us idle
        # until both a key is set AND the toggle is on.
        if self.configured:
            key_tail = self.stream_key[-4:] if len(self.stream_key) >= 4 else "?"
            log.info(f"YouTube CC: worker started (key …{key_tail}, off by default)")
        else:
            log.info("YouTube CC: worker started, no stream key yet "
                     "(set YOUTUBE_STREAM_KEY in .env, or paste a key in the operator UI)")
        async with aiohttp.ClientSession() as sess:
            self._session = sess
            try:
                while not self._stop.is_set():
                    # Block on the queue. The wait_for timeout lets us also
                    # service the "post empty caption after 10 s of silence"
                    # path — viewers shouldn't see stale captions during pauses.
                    try:
                        text, captured_at = await asyncio.wait_for(
                            self._queue.get(), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        await self._maybe_clear_after_silence()
                        await self._maybe_broadcast_status()
                        continue

                    # Toggled off between submit() and dequeue → drop.
                    if not self.enabled:
                        continue
                    # POST immediately. The body_timestamp is computed from
                    # captured_at - delay_sec inside _post(), so the operator's
                    # delay knob shifts WHERE the caption is anchored in the
                    # video timeline, not WHEN we send it.
                    await self._post(text, captured_at=captured_at)
                    await self._maybe_broadcast_status()
            finally:
                self._session = None
                log.info("YouTube CC: worker stopped")

    async def _post(self, text: str, captured_at: float | None = None) -> None:
        """POST one caption line to YouTube.

        body_timestamp = (captured_at - delay_sec) so YouTube anchors the
        caption to the video frame from when the words were (estimated to be)
        spoken — not when we received the Sarvam FINAL. This is what makes
        captions actually sync to the spoken audio for viewers (see
        operator-semantics block on the class).

        For silence-clear posts (text == ""), captured_at is None and we
        anchor at "now" — there's no specific moment of speech to align with.
        """
        self._seq += 1
        seq = self._seq
        if captured_at is None:
            anchor_t = time.time()
        else:
            anchor_t = captured_at - self.delay_sec
        dt = _dt.datetime.fromtimestamp(anchor_t, tz=_dt.timezone.utc)
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}"
        body = (ts + "\n" + text + "\n").encode("utf-8")
        params = {"cid": self.stream_key, "seq": str(seq), "lang": self.lang}
        try:
            async with self._session.post(
                YT_CC_URL, params=params, data=body,
                headers={"Content-Type": "text/plain"},
                timeout=aiohttp.ClientTimeout(total=YT_CC_HTTP_TIMEOUT_SEC),
            ) as resp:
                if resp.status == 200:
                    self._sent += 1
                    self._last_sent_at = time.time()
                    if text:
                        log.info(f"YouTube CC: seq={seq} ▶ {text!r}")
                    else:
                        log.info(f"YouTube CC: seq={seq} (cleared)")
                else:
                    self._errors += 1
                    body_resp = (await resp.text())[:200]
                    self._last_error_msg = f"HTTP {resp.status}: {body_resp}"
                    log.warning(f"YouTube CC: seq={seq} HTTP {resp.status}: {body_resp!r}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._errors += 1
            self._last_error_msg = f"{type(e).__name__}: {e}"
            log.warning(f"YouTube CC: seq={seq} POST failed: {e!r}")

    async def _maybe_clear_after_silence(self) -> None:
        """If we've sent at least one caption and 10 s have passed without
        another, POST an empty body so YouTube clears the on-screen line.
        Without this, the last caption lingers on viewer screens during pauses.
        """
        if not self.enabled or not self._last_sent_at:
            return
        if time.time() - self._last_sent_at < YT_CC_SILENCE_CLEAR_SEC:
            return
        await self._post("")
        # Reset so we don't keep posting empties every second.
        self._last_sent_at = 0.0

    async def _maybe_broadcast_status(self) -> None:
        """Push a status snapshot to the operator UI ≤ once per 1.5 s."""
        now = time.time()
        if now - self._last_status_at < 1.5:
            return
        self._last_status_at = now
        try:
            await self.broadcaster.send({"type": "yt_status", **self.status()})
        except Exception:
            pass


# ── Feed registry (per-feed YouTubeCaptionPusher instances) ──────────────────
#
# Each feed is an independent destination: its own stream key, target language,
# enable toggle, advance offset, and pusher worker task. The registry persists
# the configurable bits to `captions/outputs.json` (gitignored) so a server
# restart restores the operator's setup.
#
# Stream keys are written to disk in cleartext — outputs.json must stay
# gitignored. We expose only the last 4 chars (`stream_key_tail`) over the
# WS/REST surface; the full key never leaves the server process.

import uuid as _uuid
from dataclasses import dataclass, field

@dataclass
class FeedEntry:
    id:           str
    label:        str
    stream_key:   str
    target_lang:  str            # full code, e.g. "gu-IN"
    enabled:      bool           = False
    advance_sec:  float          = 1.5
    pusher:       "YouTubeCaptionPusher | None" = field(default=None, repr=False, compare=False)
    worker:       "asyncio.Task | None"         = field(default=None, repr=False, compare=False)

    def status_payload(self) -> dict:
        p = self.pusher
        tail = self.stream_key[-4:] if len(self.stream_key) >= 4 else ""
        base = {
            "id":              self.id,
            "label":            self.label,
            "stream_key_tail":  tail,
            "target_lang":      self.target_lang,
            "enabled":          self.enabled,
            "advance_sec":      self.advance_sec,
        }
        if p is None:
            base.update({"configured": False, "sent": 0, "errors": 0, "last_error": ""})
        else:
            s = p.status()
            base.update({
                "configured":   s["configured"],
                "sent":         s["sent"],
                "errors":       s["errors"],
                "seq":          s["seq"],
                "queue_size":   s["queue_size"],
                "last_sent_at": s["last_sent_at"],
                "last_error":   s["last_error"],
            })
        return base


class FeedRegistry:
    """Owns the list of YouTube CC feeds. Each feed has its own pusher
    instance and worker task. Persistence to outputs.json. Stream keys live
    in-memory and on-disk but only their last-4-char tail crosses the WS.
    """

    def __init__(self, broadcaster: "Broadcaster", path: Path):
        self.broadcaster = broadcaster
        self.path = path
        self.feeds: dict[str, FeedEntry] = {}

    # ── Persistence ───────────────────────────────────────────────────
    def _serialise_to_disk(self) -> dict:
        return {
            "version": 1,
            "feeds": [
                {
                    "id":          f.id,
                    "label":       f.label,
                    "stream_key":  f.stream_key,
                    "target_lang": f.target_lang,
                    "enabled":     f.enabled,
                    "advance_sec": f.advance_sec,
                } for f in self.feeds.values()
            ],
        }

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._serialise_to_disk(), indent=2))
        except Exception as e:
            log.warning(f"FeedRegistry.save: {e!r}")

    async def load(self) -> None:
        """Read outputs.json. If absent, migrate from legacy YOUTUBE_STREAM_KEY
        env var (creates a single feed) so existing deployments don't lose
        their key on first run after the multi-feed refactor."""
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for entry in data.get("feeds", []):
                    await self._materialise(
                        id          = entry.get("id") or _uuid.uuid4().hex[:8],
                        label       = entry.get("label") or "(unnamed)",
                        stream_key  = entry.get("stream_key") or "",
                        target_lang = entry.get("target_lang") or DEFAULT_TARGET_LANG,
                        enabled     = bool(entry.get("enabled", False)),
                        advance_sec = float(entry.get("advance_sec", 1.5)),
                    )
                log.info(f"FeedRegistry: loaded {len(self.feeds)} feed(s) from {self.path}")
                return
            except Exception as e:
                log.error(f"FeedRegistry.load: {e!r} — starting empty")
        # First-run scaffold: two default feeds, one for each direction's
        # caption target. The natural use case is bilingual captioning of a
        # single YouTube broadcast — viewers get to pick whichever language
        # track works for them. Both feeds get the same stream key (if any
        # legacy YOUTUBE_STREAM_KEY in env). Operator can edit/delete after.
        env_key = os.environ.get("YOUTUBE_STREAM_KEY", "").strip()
        # Don't duplicate when DEFAULT_SOURCE_LANG == DEFAULT_TARGET_LANG.
        seed_targets: list[tuple[str, str]] = []
        seed_targets.append((DEFAULT_TARGET_LANG,
                              langname(DEFAULT_TARGET_LANG) + " captions"))
        if DEFAULT_SOURCE_LANG != DEFAULT_TARGET_LANG:
            seed_targets.append((DEFAULT_SOURCE_LANG,
                                  langname(DEFAULT_SOURCE_LANG) + " captions"))
        for tgt, label in seed_targets:
            await self.create(label=label,
                              stream_key=env_key,
                              target_lang=tgt,
                              enabled=False, advance_sec=1.5)
        if env_key:
            log.info(f"FeedRegistry: migrated YOUTUBE_STREAM_KEY from env "
                     f"→ {len(seed_targets)} default feed(s) "
                     f"({', '.join(t for t,_ in seed_targets)})")
        else:
            log.info(f"FeedRegistry: seeded {len(seed_targets)} default feed(s) "
                     f"({', '.join(t for t,_ in seed_targets)}) — "
                     f"paste a stream key into each via the Outputs sidebar")

    # ── CRUD ──────────────────────────────────────────────────────────
    async def _materialise(self, *, id: str, label: str, stream_key: str,
                            target_lang: str, enabled: bool, advance_sec: float) -> FeedEntry:
        """Build pusher + worker for a feed entry and stash on the registry."""
        pusher = YouTubeCaptionPusher(stream_key, self.broadcaster)
        pusher.set_lang(yt_lang_from_sarvam(target_lang))
        pusher.delay_sec = float(advance_sec)
        pusher.enabled   = bool(enabled and pusher.configured)
        worker = asyncio.create_task(pusher.run(), name=f"yt-{id}")
        entry = FeedEntry(id=id, label=label, stream_key=stream_key,
                           target_lang=target_lang, enabled=pusher.enabled,
                           advance_sec=float(advance_sec),
                           pusher=pusher, worker=worker)
        self.feeds[id] = entry
        return entry

    async def create(self, *, label: str, stream_key: str, target_lang: str,
                     enabled: bool = False, advance_sec: float = 1.5) -> FeedEntry:
        fid = _uuid.uuid4().hex[:8]
        entry = await self._materialise(id=fid, label=label or "(unnamed)",
                                          stream_key=stream_key.strip(),
                                          target_lang=target_lang,
                                          enabled=enabled, advance_sec=advance_sec)
        self.save()
        await self._broadcast()
        return entry

    async def update(self, id: str, *, label: str | None = None,
                     stream_key: str | None = None,
                     target_lang: str | None = None,
                     enabled: bool | None = None,
                     advance_sec: float | None = None) -> FeedEntry | None:
        f = self.feeds.get(id)
        if not f:
            return None
        if label is not None:
            f.label = label
        if stream_key is not None and stream_key.strip() and stream_key.strip() != f.stream_key:
            f.stream_key = stream_key.strip()
            if f.pusher:
                f.pusher.configure(stream_key=f.stream_key)
        if target_lang is not None and target_lang in SARVAM_LANG_CODES:
            f.target_lang = target_lang
            if f.pusher:
                f.pusher.set_lang(yt_lang_from_sarvam(target_lang))
        if advance_sec is not None:
            f.advance_sec = max(0.0, float(advance_sec))
            if f.pusher:
                f.pusher.configure(delay_sec=f.advance_sec)
        if enabled is not None:
            f.enabled = bool(enabled)
            if f.pusher:
                f.pusher.configure(enabled=f.enabled)
        self.save()
        await self._broadcast()
        return f

    async def delete(self, id: str) -> bool:
        f = self.feeds.pop(id, None)
        if not f:
            return False
        if f.pusher:
            await f.pusher.stop()
        if f.worker and not f.worker.done():
            try:
                await asyncio.wait_for(f.worker, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                f.worker.cancel()
                try: await f.worker
                except Exception: pass
        self.save()
        await self._broadcast()
        return True

    # ── Views ─────────────────────────────────────────────────────────
    def list_for_wire(self) -> list[dict]:
        return [f.status_payload() for f in self.feeds.values()]

    def enabled(self) -> list[FeedEntry]:
        return [f for f in self.feeds.values() if f.enabled]

    async def _broadcast(self) -> None:
        try:
            await self.broadcaster.send({"type": "feeds_list", "feeds": self.list_for_wire()})
        except Exception:
            pass

    async def shutdown(self) -> None:
        for f in list(self.feeds.values()):
            if f.pusher:
                await f.pusher.stop()
            if f.worker and not f.worker.done():
                try:
                    await asyncio.wait_for(f.worker, timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    f.worker.cancel()
                    try: await f.worker
                    except Exception: pass


# ── Rules registry (post-translation word substitution + exclusions) ─────────
#
# A Rule is a (pattern → replacement) substitution applied to Sarvam's
# translated output BEFORE the text is broadcast / pushed to PP / queued for
# YouTube CC / multicast to Pi sidecars / written to storage. Two flavours:
#
#   - Mapping:    replacement is an arbitrary string (e.g. "stories" → "katha")
#   - Exclusion:  replacement is the ellipsis "…" (e.g. mask an unwanted word)
#
# Matching is whole-word case-insensitive by default; set `regex` to use a
# raw Python regex (still case-insensitive). Multi-word phrases are supported;
# rules are sorted longest-pattern-first so "religious stories → kathas"
# wins against a shorter "stories → katha".
#
# Edits via the operator UI flow through /api/rules and are persisted to
# `captions/rules.json` (gitignored). Hot-reload: every FINAL re-reads the
# in-memory rules list, so an edit applies to the next caption with no
# restart.

@dataclass
class Rule:
    id:          str
    pattern:     str
    replacement: str
    regex:       bool = False
    enabled:     bool = True
    _compiled:   "re.Pattern | None" = field(default=None, repr=False, compare=False)
    _error:      str = field(default="",   repr=False, compare=False)

    def compile_(self) -> None:
        """(Re)compile the rule's regex. Called on construction / edit. Stores
        the compiled pattern on `_compiled`; bad regex stays None and the
        rule is silently skipped at apply time (with `_error` set so the UI
        can surface the failure).
        """
        self._compiled = None
        self._error = ""
        if not self.enabled or not self.pattern:
            return
        try:
            if self.regex:
                self._compiled = re.compile(self.pattern, re.IGNORECASE | re.UNICODE)
            else:
                # Whole-word case-insensitive literal match. `\b` is
                # Python's word-boundary anchor and behaves correctly for
                # ASCII English (our caption output language).
                self._compiled = re.compile(
                    r"\b" + re.escape(self.pattern) + r"\b",
                    re.IGNORECASE | re.UNICODE,
                )
        except re.error as e:
            self._error = str(e)
            log.warning(f"Rule {self.id!r}: bad pattern {self.pattern!r}: {e}")

    def to_wire(self) -> dict:
        return {
            "id":          self.id,
            "pattern":     self.pattern,
            "replacement": self.replacement,
            "regex":       self.regex,
            "enabled":     self.enabled,
            "is_exclusion": self.replacement == "…",
            "error":       self._error,
        }

    def to_disk(self) -> dict:
        return {
            "id":          self.id,
            "pattern":     self.pattern,
            "replacement": self.replacement,
            "regex":       self.regex,
            "enabled":     self.enabled,
        }


def apply_rules(text: str, rules: list[Rule]) -> tuple[str, list[str]]:
    """Run every enabled rule against `text` in longest-pattern-first order.
    Returns the post-substitution text and the list of rule IDs that actually
    fired (a rule "fires" only when at least one match was replaced).

    Order matters: longer patterns ("religious stories") win over shorter
    ones ("stories") because the long match is consumed first.

    Failure mode: text empty / no rules / no compiled rules → return as-is.
    """
    if not text or not rules:
        return text, []
    fired: list[str] = []
    # Sort longest pattern first so multi-word phrases beat their substrings.
    # Stable secondary sort by rule id keeps behaviour deterministic.
    ordered = sorted(
        [r for r in rules if r.enabled and r._compiled is not None],
        key=lambda r: (-len(r.pattern), r.id),
    )
    for rule in ordered:
        new_text, n = rule._compiled.subn(rule.replacement, text)
        if n > 0:
            fired.append(rule.id)
            text = new_text
    return text, fired


class RulesRegistry:
    """Owns the substitution rules list. Mirrors FeedRegistry: persisted to
    `rules.json`, broadcast over WS on every change, hot-reloaded in-memory
    on every edit.

    On first run, if rules.json is absent and `rules.starter.json` exists
    next to live_captions.py, the starter is copied into rules.json to seed
    the new install. Otherwise the registry starts empty.
    """

    def __init__(self, broadcaster: "Broadcaster", path: Path, starter_path: Path):
        self.broadcaster  = broadcaster
        self.path         = path
        self.starter_path = starter_path
        self.rules: dict[str, Rule] = {}

    # ── Persistence ───────────────────────────────────────────────────
    def _serialise_to_disk(self) -> dict:
        return {
            "version": 1,
            "rules":   [r.to_disk() for r in self.rules.values()],
        }

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._serialise_to_disk(), indent=2,
                                            ensure_ascii=False))
        except Exception as e:
            log.warning(f"RulesRegistry.save: {e!r}")

    async def load(self) -> None:
        """Read rules.json. If absent, seed from rules.starter.json (if it
        exists) so a fresh install can ship with a preset dictionary out of
        the box. Otherwise start empty.
        """
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for entry in data.get("rules", []):
                    self._add_from_dict(entry)
                log.info(f"RulesRegistry: loaded {len(self.rules)} rule(s) from {self.path}")
                return
            except Exception as e:
                log.error(f"RulesRegistry.load: {e!r} — starting empty")
                self.rules.clear()
        # First-run seed from starter dictionary.
        if self.starter_path.exists():
            try:
                data = json.loads(self.starter_path.read_text())
                for entry in data.get("rules", []):
                    self._add_from_dict(entry)
                self.save()
                log.info(f"RulesRegistry: seeded {len(self.rules)} rule(s) from "
                         f"{self.starter_path.name} → {self.path.name}")
                return
            except Exception as e:
                log.warning(f"RulesRegistry: failed to seed from starter: {e!r}")
        log.info("RulesRegistry: starting empty (no rules.json, no starter)")

    def _add_from_dict(self, entry: dict) -> Rule:
        rid = entry.get("id") or _uuid.uuid4().hex[:8]
        rule = Rule(
            id          = rid,
            pattern     = (entry.get("pattern") or "").strip(),
            replacement = entry.get("replacement") if entry.get("replacement") is not None else "",
            regex       = bool(entry.get("regex", False)),
            enabled     = bool(entry.get("enabled", True)),
        )
        rule.compile_()
        self.rules[rid] = rule
        return rule

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(self, *, pattern: str, replacement: str,
                     regex: bool = False, enabled: bool = True) -> Rule:
        rule = self._add_from_dict({
            "pattern": pattern, "replacement": replacement,
            "regex": regex, "enabled": enabled,
        })
        self.save()
        await self._broadcast()
        return rule

    async def update(self, id: str, *, pattern: str | None = None,
                     replacement: str | None = None,
                     regex: bool | None = None,
                     enabled: bool | None = None) -> Rule | None:
        r = self.rules.get(id)
        if not r:
            return None
        if pattern is not None:
            r.pattern = pattern.strip()
        if replacement is not None:
            r.replacement = replacement
        if regex is not None:
            r.regex = bool(regex)
        if enabled is not None:
            r.enabled = bool(enabled)
        r.compile_()
        self.save()
        await self._broadcast()
        return r

    async def delete(self, id: str) -> bool:
        if id not in self.rules:
            return False
        del self.rules[id]
        self.save()
        await self._broadcast()
        return True

    # ── Views ─────────────────────────────────────────────────────────
    def all(self) -> list[Rule]:
        return list(self.rules.values())

    def list_for_wire(self) -> list[dict]:
        return [r.to_wire() for r in self.rules.values()]

    def label_for(self, rid: str) -> str:
        """Short human label for a rule, used in the transcript badge tooltip."""
        r = self.rules.get(rid)
        if not r:
            return rid
        return f"{r.pattern} → {r.replacement}"

    async def _broadcast(self) -> None:
        try:
            await self.broadcaster.send({"type": "rules_list", "rules": self.list_for_wire()})
        except Exception:
            pass


# ── Session recorder (per-session JSONL + post-stop SRT) ─────────────────────
#
# Started on /api/start, stopped on /api/stop. One JSONL file per session at
# `captions/results/<APP_NAME>-<ISO local timestamp>.jsonl`. Records every
# FINAL with raw + corrected text + which rules fired; also captures a
# session header (Sarvam config, audio source, language pair) for forensic
# context. On stop, walks the file and writes a sibling .srt with cues
# anchored to session start so the file is upload-ready in YouTube Studio.

def _slugify_app_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "captions").lower()).strip("-")
    return s or "captions"

def _iso_utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()

def _srt_timestamp(seconds: float) -> str:
    if seconds < 0: seconds = 0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    s  = (total_ms // 1000) % 60
    m  = (total_ms // 60000) % 60
    h  = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class SessionRecorder:
    """One JSONL file per Start→Stop session. Buffered writes auto-flush
    after every record so a crash mid-session still leaves a usable file.

    Lifecycle:
        start(header)   → opens results/<slug>-<ts>.jsonl, writes session_start
        write_final(...) → one line per FINAL caption
        write_event(...) → one line per VAD event (START/END_SPEECH) or other
        stop()          → writes session_stop, closes file, emits sibling .srt
    """

    def __init__(self, results_dir: Path, app_name: str):
        self.results_dir = results_dir
        self.app_name    = app_name
        self.path:       Path | None = None
        self.srt_path:   Path | None = None
        self.fh                       = None
        self.start_wall: float | None = None
        self.start_iso:  str | None   = None
        self.final_count: int          = 0
        self.partial_count: int        = 0

    def is_active(self) -> bool:
        return self.fh is not None

    def start(self, header_extra: dict) -> Path | None:
        if self.is_active():
            log.warning("SessionRecorder.start: already active — stopping previous session first")
            self.stop()
        try:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            now_local = _dt.datetime.now()
            slug = _slugify_app_name(self.app_name)
            stamp = now_local.strftime("%Y-%m-%dT%H-%M-%S")
            self.path = self.results_dir / f"{slug}-{stamp}.jsonl"
            self.srt_path = self.path.with_suffix(".srt")
            self.fh = self.path.open("w", encoding="utf-8")
            self.start_wall = time.time()
            self.start_iso  = _iso_utc_now()
            self.final_count = 0
            self.partial_count = 0
            self._write({
                "type":       "session_start",
                "ts":         self.start_iso,
                "app_name":   self.app_name,
                **header_extra,
            })
            log.info(f"SessionRecorder: recording to {self.path}")
            return self.path
        except Exception as e:
            log.error(f"SessionRecorder.start: {e!r}")
            self.fh = None
            self.path = None
            return None

    def write_final(self, *, raw: str, corrected: str, rules_fired: list[str],
                    source_lang: str, target_lang: str,
                    audio_level: float | None = None) -> None:
        if not self.fh: return
        self._write({
            "type":         "final",
            "ts":           _iso_utc_now(),
            "elapsed_s":    self._elapsed(),
            "raw":          raw,
            "corrected":    corrected,
            "rules_fired":  rules_fired,
            "source_lang":  source_lang,
            "target_lang":  target_lang,
            "audio_level":  audio_level,
        })
        self.final_count += 1

    def write_event(self, signal: str, extra: dict | None = None) -> None:
        """VAD START_SPEECH / END_SPEECH or other lifecycle markers."""
        if not self.fh: return
        rec = {"type": "event", "ts": _iso_utc_now(),
                "elapsed_s": self._elapsed(), "signal": signal}
        if extra:
            rec.update(extra)
        self._write(rec)
        self.partial_count += 1

    def _elapsed(self) -> float:
        return (time.time() - self.start_wall) if self.start_wall else 0.0

    def _write(self, record: dict) -> None:
        try:
            self.fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.fh.flush()
        except Exception as e:
            log.warning(f"SessionRecorder._write: {e!r}")

    def stop(self) -> tuple[Path | None, Path | None]:
        """Close the JSONL and emit a sibling SRT. Returns (jsonl_path,
        srt_path) — either may be None if the operation failed."""
        if not self.fh:
            return None, None
        try:
            self._write({
                "type":        "session_stop",
                "ts":          _iso_utc_now(),
                "elapsed_s":   self._elapsed(),
                "final_count": self.final_count,
                "event_count": self.partial_count,
            })
            self.fh.close()
        except Exception as e:
            log.warning(f"SessionRecorder.stop write/close: {e!r}")
        jsonl_path = self.path
        srt_path   = None
        try:
            srt_path = self._emit_srt(jsonl_path, self.srt_path)
        except Exception as e:
            log.warning(f"SessionRecorder.stop SRT emit: {e!r}")
        # Reset state
        self.fh = None
        self.path = None
        self.srt_path = None
        self.start_wall = None
        self.start_iso = None
        self.final_count = 0
        self.partial_count = 0
        return jsonl_path, srt_path

    @staticmethod
    def _emit_srt(jsonl_path: Path | None, srt_path: Path | None) -> Path | None:
        if not jsonl_path or not jsonl_path.exists() or not srt_path:
            return None
        # Collect FINALs as (elapsed_s, text) — `corrected` is the
        # post-rules text actually shown to the audience; that's what
        # belongs in the SRT track operators upload to YouTube.
        cues: list[tuple[float, str]] = []
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("type") != "final":
                    continue
                text = (rec.get("corrected") or "").strip()
                if not text:
                    continue
                cues.append((float(rec.get("elapsed_s") or 0.0), text))
        if not cues:
            return None
        # Each cue's end-time = next cue's start (so the previous caption
        # stays on-screen until replaced), capped at 6.0 s. Final cue uses
        # a read-speed estimate (~15 chars/sec).
        with srt_path.open("w", encoding="utf-8") as f:
            for i, (start, text) in enumerate(cues):
                if i + 1 < len(cues):
                    nxt = cues[i+1][0]
                    dur = max(0.5, min(6.0, nxt - start))
                else:
                    dur = max(1.0, min(6.0, len(text) / 15.0))
                end = start + dur
                f.write(f"{i+1}\n")
                f.write(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n")
                f.write(text + "\n\n")
        log.info(f"SessionRecorder: wrote {len(cues)} SRT cues to {srt_path}")
        return srt_path


# ── VOD reprocess job registry ───────────────────────────────────────────────
#
# Wraps `tools.vod_pipeline.VodPipeline` in a job queue so the operator UI
# can fire-and-forget reprocess requests and watch progress over WS.
#
# One worker task drains jobs serially — GCP STT is the only meaningful
# bottleneck and runs server-side, so adding parallelism here wouldn't
# help and would just complicate cancellation / disk I/O.
#
# Job state persists to `captions/vod-jobs.json` (gitignored) so a server
# restart can show a job-history list even though in-flight jobs don't
# survive (they'd need to be re-submitted).

@dataclass
class VodJob:
    id:          str
    video_url:   str
    video_id:    str
    status:      str   = "queued"      # queued | running | awaiting_ranges | done | failed | cancelled
    stage:       str   = "queued"      # see tools.vod_pipeline.Stage
    stage_label: str   = "Queued"
    stage_pct:   float | None = None   # 0..1 within current stage
    stage_detail: str  = ""
    stage_elapsed_s: float = 0.0
    created_at:  str   = ""
    started_at:  str   = ""
    finished_at: str   = ""
    error:       str   = ""
    # Operator-selected transcription ranges (list of {start_s, end_s}).
    # Empty / unset = transcribe full video.
    ranges:      list  = field(default_factory=list)
    # Populated as we go — even before completion, so the UI's range
    # editor can show the MP4 in the video player.
    mp4_url:     str   = ""
    # Populated on completion
    cue_count:   int   = 0
    rules_fired_count: int = 0
    srt_url:     str   = ""            # /results/vod-<id>/vod-<id>-en.srt
    preview_url: str   = ""            # /results/vod-<id>/vod-<id>-preview.html

    def to_wire(self) -> dict:
        # Identity 1:1 — every field is wire-safe. Caller can json.dumps().
        return {k: v for k, v in self.__dict__.items()}


class VodJobRegistry:
    """Owns the list of VOD reprocess jobs. One worker task; serial
    execution. Persists to vod-jobs.json. Broadcasts state on every
    transition via the existing Broadcaster.
    """

    def __init__(self, broadcaster: "Broadcaster", path: Path,
                  results_dir: Path, rules_path: Path):
        self.broadcaster = broadcaster
        self.path        = path
        self.results_dir = results_dir
        self.rules_path  = rules_path
        self.jobs: dict[str, VodJob] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._current_id: str | None = None
        self._current_pipeline_task: asyncio.Task | None = None
        # Live pipeline instance for the currently-running job — exposed
        # so the resume-with-ranges HTTP handler can deliver operator
        # input to the paused pipeline.
        self._current_pipeline: "object | None" = None

    # ── Persistence ───────────────────────────────────────────────────
    def _serialise_to_disk(self) -> dict:
        # Only persist jobs that have reached a terminal state — in-flight
        # jobs can't resume across restart so saving them just confuses
        # the next session's UI.
        return {
            "version": 1,
            "jobs": [
                j.to_wire() for j in self.jobs.values()
                if j.status in ("done", "failed", "cancelled")
            ],
        }

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._serialise_to_disk(), indent=2,
                                            ensure_ascii=False))
        except Exception as e:
            log.warning(f"VodJobRegistry.save: {e!r}")

    async def load(self) -> None:
        if not self.path.exists():
            log.info(f"VodJobRegistry: no {self.path.name} — starting empty")
            return
        try:
            data = json.loads(self.path.read_text())
            for entry in data.get("jobs", []):
                j = VodJob(**{k: v for k, v in entry.items() if k in VodJob.__dataclass_fields__})
                self.jobs[j.id] = j
            log.info(f"VodJobRegistry: loaded {len(self.jobs)} historical job(s)")
        except Exception as e:
            log.error(f"VodJobRegistry.load: {e!r} — starting empty")

    # ── CRUD ──────────────────────────────────────────────────────────
    async def create(self, video_url: str) -> VodJob:
        """Parse the URL, enqueue, return the new job. Raises ValueError
        on a malformed URL."""
        from tools.vod_pipeline import parse_video_id
        video_id = parse_video_id(video_url)
        jid = _uuid.uuid4().hex[:8]
        # mp4_url is deterministic from video_id; pre-fill so the UI's
        # video player has something to point at the moment the MP4
        # finishes downloading (browser handles 404 → reload gracefully
        # once the file lands).
        job = VodJob(
            id          = jid,
            video_url   = video_url,
            video_id    = video_id,
            created_at  = _iso_utc_now(),
            mp4_url     = f"/results/vod-{video_id}/vod-{video_id}.mp4",
        )
        self.jobs[jid] = job
        await self._queue.put(jid)
        log.info(f"VodJobRegistry: queued job {jid} for video {video_id}")
        await self._broadcast(job)
        return job

    async def resume_with_ranges(self, jid: str, ranges: list[tuple[float, float]]) -> bool:
        """Operator-driven resume — delivers ranges to the paused pipeline.
        Empty list = transcribe full video. Returns False if the job isn't
        currently awaiting ranges (e.g. wrong id, already running,
        already done)."""
        job = self.jobs.get(jid)
        if not job:
            return False
        if job.status != "awaiting_ranges":
            return False
        if self._current_id != jid or self._current_pipeline is None:
            return False
        # Persist on the job so the UI can show the selection later.
        job.ranges = [{"start_s": s, "end_s": e} for s, e in ranges]
        self._current_pipeline.resume_with_ranges(ranges)
        self.save()
        await self._broadcast(job)
        return True

    async def cancel(self, jid: str) -> bool:
        """Cancel a queued or running job. Queued = just mark cancelled.
        Running = cancel the pipeline task (sync GCP calls inside an
        executor can't be hard-cancelled; the job state flips to
        cancelled and the executor thread finishes in the background)."""
        job = self.jobs.get(jid)
        if not job:
            return False
        if job.status not in ("queued", "running"):
            return False
        if self._current_id == jid and self._current_pipeline_task:
            self._current_pipeline_task.cancel()
        job.status      = "cancelled"
        job.stage       = "cancelled"
        job.stage_label = "Cancelled by operator"
        job.finished_at = _iso_utc_now()
        self.save()
        await self._broadcast(job)
        return True

    async def delete(self, jid: str) -> bool:
        """Remove from the in-memory + on-disk job list. Does NOT delete
        files on disk (operator can do that manually if they want to
        reclaim space)."""
        job = self.jobs.get(jid)
        if not job:
            return False
        if job.status == "running":
            # Don't allow deleting a running job — would leave the worker
            # holding a reference to a dropped job.
            return False
        del self.jobs[jid]
        self.save()
        try:
            await self.broadcaster.send({"type": "vod_job_deleted", "id": jid})
        except Exception:
            pass
        return True

    # ── Views ─────────────────────────────────────────────────────────
    def list_for_wire(self) -> list[dict]:
        # Newest first so the UI's history list shows recent runs on top.
        return [j.to_wire() for j in sorted(
            self.jobs.values(), key=lambda j: j.created_at, reverse=True
        )]

    async def _broadcast(self, job: VodJob) -> None:
        try:
            await self.broadcaster.send({"type": "vod_job", "job": job.to_wire()})
        except Exception:
            pass

    # ── Worker ────────────────────────────────────────────────────────
    def start_worker(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker_loop(), name="vod-worker")

    async def _worker_loop(self) -> None:
        from tools.vod_pipeline import VodPipeline, check_prereqs, detect_project
        log.info("VOD worker loop started")
        while True:
            try:
                jid = await self._queue.get()
            except asyncio.CancelledError:
                log.info("VOD worker loop cancelled")
                return
            job = self.jobs.get(jid)
            if not job or job.status == "cancelled":
                continue
            self._current_id = jid
            try:
                # Prereqs are re-checked per job so an in-flight tool
                # update / creds rotation doesn't silently miss.
                check_prereqs()
                project = detect_project()
                bucket  = os.environ.get("GCS_BUCKET", "").strip()
                if not bucket:
                    raise RuntimeError(
                        "GCS_BUCKET env var unset — set it in .env "
                        "(e.g. GCS_BUCKET=<your-gcs-bucket>) and restart"
                    )
                if not project:
                    raise RuntimeError("Could not detect GCP project (set GCP_PROJECT or run `gcloud config set project <id>`)")

                job.status      = "running"
                job.started_at  = _iso_utc_now()
                await self._broadcast(job)

                async def _on_progress(ev):
                    job.stage           = ev.stage.value
                    job.stage_label     = ev.detail or job.stage
                    job.stage_pct       = ev.pct
                    job.stage_detail    = ev.detail
                    job.stage_elapsed_s = ev.elapsed_s
                    # The awaiting-ranges stage flips the job-level
                    # status so the React Reprocess tab knows to render
                    # the range editor instead of the progress stepper.
                    # Once ranges are delivered (via resume_with_ranges)
                    # the pipeline emits another AWAITING_RANGES event
                    # with pct=1.0; that flips status back to running.
                    if ev.stage.value == "awaiting_ranges" and (ev.pct is None or ev.pct < 1.0):
                        job.status = "awaiting_ranges"
                    elif job.status == "awaiting_ranges":
                        job.status = "running"
                    await self._broadcast(job)

                pipeline = VodPipeline(
                    video_id    = job.video_id,
                    bucket      = bucket,
                    project     = project,
                    results_dir = self.results_dir,
                    rules_path  = self.rules_path,
                    on_progress = _on_progress,
                )
                self._current_pipeline = pipeline
                self._current_pipeline_task = asyncio.create_task(
                    pipeline.run(), name=f"vod-{job.id}",
                )
                result = await self._current_pipeline_task

                # Success — populate result fields + flip status
                job.status            = "done"
                job.stage             = "done"
                job.stage_label       = "Done"
                job.stage_pct         = 1.0
                job.finished_at       = _iso_utc_now()
                job.cue_count         = result.cue_count
                job.rules_fired_count = result.rules_fired_count
                # /results/... is the URL prefix mounted in main().
                rel = f"/results/{result.out_dir.name}"
                job.mp4_url      = f"{rel}/{result.mp4_path.name}"
                job.srt_url      = f"{rel}/{result.srt_path.name}"
                job.preview_url  = f"{rel}/{result.html_path.name}"
                log.info(f"VOD job {job.id} done: {result.cue_count} cues, "
                         f"{result.rules_fired_count} with rules fired")

            except asyncio.CancelledError:
                # Operator cancellation: status already flipped by cancel()
                log.info(f"VOD job {job.id} cancelled")
                if job.status != "cancelled":
                    job.status      = "cancelled"
                    job.stage       = "cancelled"
                    job.stage_label = "Cancelled"
                    job.finished_at = _iso_utc_now()
            except Exception as e:
                log.error(f"VOD job {job.id} failed: {e!r}", exc_info=True)
                job.status      = "failed"
                job.stage       = "failed"
                job.stage_label = "Failed"
                job.error       = str(e)
                job.finished_at = _iso_utc_now()
            finally:
                self._current_id            = None
                self._current_pipeline_task = None
                self._current_pipeline      = None
                self.save()
                await self._broadcast(job)

    async def shutdown(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass


# ── Sarvam streaming loop ─────────────────────────────────────────────────────

async def sarvam_loop(audio_gen, broadcaster: Broadcaster, use_pp: bool, stop_event: asyncio.Event,
                      state: dict, sarvam_cfg: dict | None = None, gate_cfg: dict | None = None):
    """Sarvam streaming session supervisor.

    Reads the current (source, target) lang pair from
    `state["source"]` / `state["target"]` on each reconnect iteration so a
    mid-session ⇆ flip or any other direction change picks up automatically.
    Stashes the live ws on `state["sarvam_ws"]` so the flip handler can
    close it to force a reconnect.
    """
    from sarvamai import AsyncSarvamAI

    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key:
        log.error("SARVAM_API_KEY not set")
        return

    # Base kwargs from the UI start payload — model + VAD knobs survive
    # across direction changes. Mode + language_code get derived per-
    # iteration from state["source"]/state["target"] so a flip / dropdown
    # change takes effect on reconnect.
    cfg = sarvam_cfg or {}
    base_kwargs = dict(
        model                = cfg.get("model",                "saaras:v3"),
        sample_rate          = cfg.get("sample_rate",          16000),
        input_audio_codec    = cfg.get("input_audio_codec",    "pcm_s16le"),
        high_vad_sensitivity = bool(cfg.get("high_vad_sensitivity", True)),
        vad_signals          = bool(cfg.get("vad_signals",     True)),
    )

    # Client-side gate (not sent to Sarvam — pre-filters silence locally).
    gate = gate_cfg or {}
    gate_peak_threshold = float(gate.get("silence_threshold", 0.010))
    gate_hangover_sec   = float(gate.get("hangover_sec",      1.5))
    log.info(f"Client silence gate: peak ≥ {gate_peak_threshold*100:.2f}% of full-scale, "
             f"hangover {gate_hangover_sec:.2f}s")

    client = AsyncSarvamAI(api_subscription_key=api_key)
    pp_session = aiohttp.ClientSession() if use_pp else None
    pp_id: str | None = None
    # Shared aiohttp session for Mayura POST /translate calls during en_gu
    # sessions. One pooled session ⇒ keep-alive, one TLS handshake instead
    # of per-FINAL. Kept regardless of starting direction so a flip mid-
    # session doesn't need to spin one up.
    mt_session = aiohttp.ClientSession()

    if pp_session:
        pp_id = await get_or_create_pp_message(pp_session)
        log.info(f"ProPresenter message id: {pp_id}")

    # ── Audio device lifetime decoupled from session lifetime ──────────────
    # The raw `audio_gen` opens the mic via `with sd.InputStream(...)`. If a
    # consumer (the sender task) is cancelled mid-`async for`, CancelledError
    # propagates into the generator, the `with` exits, the device closes,
    # and the generator is then permanently exhausted. That's catastrophic
    # for reconnects (e.g. mid-session direction flip) where we want the mic
    # to keep capturing while a new Sarvam WS is established.
    #
    # Fix: pump `audio_gen` into a shared asyncio.Queue at the loop level.
    # Each session gets a fresh `_queue_consumer()` iterator that pulls
    # from the queue. Cancelling that consumer is harmless — the pump task
    # and the underlying device survive.
    audio_q: asyncio.Queue = asyncio.Queue(maxsize=16)
    audio_pump_done = asyncio.Event()
    async def _audio_pump():
        try:
            async for pcm, ts in audio_gen:
                if stop_event.is_set():
                    break
                try:
                    audio_q.put_nowait((pcm, ts))
                except asyncio.QueueFull:
                    # Backpressure (e.g. flip reconnect gap): drop oldest so
                    # the freshest audio is what Sarvam sees on reconnect.
                    try:
                        audio_q.get_nowait()
                        audio_q.put_nowait((pcm, ts))
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"audio pump: {type(e).__name__}: {e!r}")
        finally:
            audio_pump_done.set()
            log.info("audio pump: ended")

    audio_pump_task = asyncio.create_task(_audio_pump(), name="audio-pump")

    async def _queue_consumer():
        # Fresh per-session iterator. Yields until stop_event fires or the
        # pump terminates (e.g. device disconnected). Cancellation closes
        # only this iterator, NOT the underlying audio_gen.
        while True:
            if audio_pump_done.is_set() and audio_q.empty():
                return
            try:
                pcm, ts = await asyncio.wait_for(audio_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield pcm, ts

    # Sarvam's WS sometimes drops with `no close frame received or sent`
    # (server-side idle timeout, transient network blip). Retry the connect
    # with exponential backoff so a brief drop doesn't end the session —
    # the operator only sees an interruption if Sarvam stays unreachable
    # past the backoff cap (8 s). The audio_gen, captionText state on each
    # browser tab, and PP message id all survive across reconnects.
    import websockets.exceptions as _wse
    backoff_sec = 0.5
    attempt = 0
    try:
        while not stop_event.is_set():
            attempt += 1
            # Build connect_kwargs from the current (source, target) every
            # iteration. The flip / direction-change handler mutates state
            # and closes the live ws, which falls through to here with new
            # values.
            source = state.get("source", DEFAULT_SOURCE_LANG)
            target = state.get("target", DEFAULT_TARGET_LANG)
            pipe   = derive_pipeline(source, target)
            connect_kwargs = dict(base_kwargs)
            connect_kwargs["mode"]          = pipe["sarvam_mode"]
            connect_kwargs["language_code"] = pipe["sarvam_lang"]
            log.info(f"Sarvam connect kwargs (source={source!r} target={target!r}): {connect_kwargs}")
            log.info(("Re-c" if attempt > 1 else "C") + f"onnecting to Sarvam… (attempt {attempt})")
            # Bail out early if the audio pump has died — there's no point
            # reconnecting to Sarvam if no audio will arrive.
            if audio_pump_done.is_set() and audio_q.empty():
                log.warning("audio pump exited and queue is empty — ending sarvam_loop")
                break
            try:
                await _sarvam_session(
                    client, connect_kwargs, _queue_consumer(), broadcaster,
                    pp_session, pp_id, stop_event,
                    gate_peak_threshold, gate_hangover_sec, attempt,
                    source=source, target=target, pipeline=pipe,
                    mt_session=mt_session,
                    api_key=api_key, state=state,
                )
                if stop_event.is_set():
                    break
                # Returned without exception (WS closed cleanly) but the
                # operator didn't stop — could be Sarvam idle-timeout or a
                # flip that closed the ws on purpose. Either way, reconnect
                # immediately, not after the full backoff.
                log.info("Sarvam WS closed cleanly — reconnecting")
                backoff_sec = 0.5
            except asyncio.CancelledError:
                raise
            except _wse.ConnectionClosed as e:
                log.warning(f"Sarvam WS dropped: {type(e).__name__}: {e}  "
                            f"— reconnect in {backoff_sec:.1f}s")
            except Exception as e:
                log.error(f"Sarvam session error: {e!r}", exc_info=True)
                # Notify the operator's browser tab — the page WS status pill
                # already shows our reconnect, but a one-line debug entry helps.

            if stop_event.is_set():
                break

            # Wait for the backoff or for the operator to press Stop —
            # whichever happens first.
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff_sec)
                break   # stop_event fired during the wait
            except asyncio.TimeoutError:
                pass
            backoff_sec = min(backoff_sec * 1.7, 8.0)
    finally:
        # Stop the audio pump, then close the underlying audio_gen so the
        # sd.InputStream `with` block exits cleanly. Without an explicit
        # aclose() the device stream stays alive (the `with` only exits on
        # GC) and we get audio-queue-full warnings for tens of seconds.
        if not audio_pump_task.done():
            audio_pump_task.cancel()
            try:
                await audio_pump_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await audio_gen.aclose()
        except Exception:
            pass
        if pp_session:
            await pp_session.close()
        try:
            await mt_session.close()
        except Exception:
            pass
        await broadcaster.send({"type": "stopped"})
        await broadcaster.send({"type": "clear"})
        # Clear the live-ws stash so /api/direction in a stopped state is a no-op.
        state["sarvam_ws"] = None
        # Close the session recorder and emit the sibling SRT. Broadcast
        # the paths so the operator UI can show "Saved to …" once the
        # files land. Recorder may have been stopped already if the
        # operator hit Stop and the loop drained naturally afterward.
        recorder: SessionRecorder | None = state.get("session_recorder") if state else None
        if recorder is not None and recorder.is_active():
            jsonl_path, srt_path = recorder.stop()
            try:
                await broadcaster.send({
                    "type":   "session_saved",
                    "jsonl":  str(jsonl_path) if jsonl_path else None,
                    "srt":    str(srt_path)   if srt_path   else None,
                    # Static-mount URLs so the React UI can download/preview
                    # without needing to know the on-disk path.
                    "jsonl_url": f"/results/{jsonl_path.name}" if jsonl_path else None,
                    "srt_url":   f"/results/{srt_path.name}"   if srt_path   else None,
                })
            except Exception:
                pass
        log.info("Sarvam loop ended")


async def _mayura_translate(session: aiohttp.ClientSession, api_key: str,
                            text: str, source_lang: str, target_lang: str,
                            model: str | None = None,
                            timeout_sec: float = 5.0) -> str:
    """One-shot Sarvam Translate call.

    `model` selects Mayura (default — best for EN + 10 Indic) vs
    "sarvam-translate" (broader coverage incl. the 22 official Indic langs).
    Returns translated text on success; returns the source `text` unchanged
    on any failure (caller decides whether that's acceptable — broadcasting
    the source is preferable to broadcasting nothing).
    """
    payload: dict = {
        "input":                text,
        "source_language_code": source_lang,
        "target_language_code": target_lang,
    }
    if model:
        payload["model"] = model
    headers = {
        "api-subscription-key": api_key,
        "Content-Type":         "application/json",
    }
    try:
        async with session.post(
            SARVAM_TRANSLATE_URL,
            json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            if resp.status != 200:
                body_resp = (await resp.text())[:200]
                log.warning(f"Mayura: HTTP {resp.status}: {body_resp!r} — falling back to source")
                return text
            data = await resp.json()
            # API field name is `translated_text` per the documented schema.
            out = (data.get("translated_text") or "").strip()
            if not out:
                log.warning(f"Mayura: empty translated_text, payload={data!r} — falling back to source")
                return text
            return out
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"Mayura: {type(e).__name__}: {e} — falling back to source")
        return text


async def _sarvam_session(client, connect_kwargs: dict, audio_gen,
                          broadcaster: "Broadcaster",
                          pp_session, pp_id: str | None,
                          stop_event: asyncio.Event,
                          gate_peak_threshold: float, gate_hangover_sec: float,
                          attempt: int,
                          *,
                          source: str = DEFAULT_SOURCE_LANG,
                          target: str = DEFAULT_TARGET_LANG,
                          pipeline: dict | None = None,
                          mt_session: aiohttp.ClientSession | None = None,
                          api_key: str = "",
                          state: dict | None = None) -> None:
    """One Sarvam WebSocket session. Returns when WS closes cleanly or when
    stop_event is set. Raises websockets.exceptions.ConnectionClosed (or
    other) when the WS dies unexpectedly — the outer reconnect loop in
    sarvam_loop catches that and retries.

    Pipeline:
        if pipeline.needs_mayura:
            Saaras returns text in pipeline.saaras_output_lang; the receive
            loop then fans out a Mayura call per unique target language
            needed (display + every enabled feed) in parallel and broadcasts
            one tagged final per target.
        else:
            Saaras already returns text in the display target language;
            broadcast it as-is. Each feed targeting that same language gets
            the same text; feeds wanting OTHER languages do their own
            Mayura step (the receive loop handles both branches uniformly).
    """
    import websockets.exceptions as _wse
    pipe        = pipeline or derive_pipeline(source, target)
    saaras_out  = pipe["saaras_output_lang"]
    needs_mt    = pipe["needs_mayura"]
    if mt_session is None or not api_key:
        # Without a Mayura session we can only do display targets that match
        # the Saaras output language. Log loudly so the operator sees no-MT
        # mode in the debug panel.
        log.error("_sarvam_session: mt_session or api_key missing — Mayura disabled")
        mt_disabled = True
    else:
        mt_disabled = False

    async with client.speech_to_text_streaming.connect(**connect_kwargs) as ws:
        log.info(f"Sarvam connected (source={source} → target={target}, "
                 f"saaras_out={saaras_out}, mayura={'on' if needs_mt and not mt_disabled else 'off'}). Listening…")
        # Stash live ws for the flip handler.
        if state is not None:
            state["sarvam_ws"] = ws
        # Operator UI can show a brief "RECONNECTED" pill instead of a stale
        # ERROR. Browser ignores unknown msg types.
        try:
            await broadcaster.send({"type": "reconnected", "attempt": attempt})
        except Exception:
            pass

        async def sender():
            import numpy as np
            log.info("sender: started, waiting for first chunk from audio_gen")
            # Threshold + hangover come from the operator UI (defaults: 1%
            # peak, 1.5 s hangover). Tunable per-session via the Sarvam
            # config row without restarting the server.
            PEAK_THRESHOLD = gate_peak_threshold
            HANGOVER_SEC   = gate_hangover_sec
            LEVEL_BROADCAST_HZ = 4   # ~250 ms cadence to the meter

            sent, skipped, slow_sends = 0, 0, 0
            last_report = time.time()
            last_active = 0.0
            first_send_logged = False
            silence_streak_logged = False
            level_peak = 0.0
            last_level_broadcast = 0.0
            async for pcm, _ in audio_gen:
                if stop_event.is_set():
                    break

                peak = float(np.abs(np.frombuffer(pcm, dtype=np.int16)).max()) / 32767.0
                if peak > level_peak:
                    level_peak = peak

                now = time.time()
                if now - last_level_broadcast >= (1.0 / LEVEL_BROADCAST_HZ):
                    try:
                        await broadcaster.send({"type": "level", "peak": level_peak})
                    except Exception:
                        pass
                    # Stash latest peak so the SessionRecorder can attach
                    # it to each FINAL record — a single column to spot
                    # mic-mute / mic-gain incidents in the JSONL after the
                    # fact.
                    if state is not None:
                        state["last_audio_level"] = level_peak
                    level_peak = 0.0
                    last_level_broadcast = now

                if peak >= PEAK_THRESHOLD:
                    last_active = now
                    silence_streak_logged = False
                if now - last_active > HANGOVER_SEC:
                    skipped += 1
                    if not silence_streak_logged and last_active and (now - last_active) > 10:
                        log.warning(
                            f"sender: 10s of silence — last loud chunk was "
                            f"{now - last_active:.0f}s ago. Mic gain too low? "
                            f"Threshold = {PEAK_THRESHOLD*100:.1f}% of full-scale."
                        )
                        silence_streak_logged = True
                    continue

                sent += 1
                if not first_send_logged:
                    log.info(f"sender: got chunk 1 ({len(pcm)} bytes, peak={peak*100:.1f}%) → ws.transcribe()")
                t_pre = time.time()
                try:
                    # Per-message `encoding` is a pydantic literal that only
                    # accepts "audio/wav". The real codec is at connect-time.
                    await ws.transcribe(
                        audio=base64.b64encode(pcm).decode(),
                        encoding="audio/wav",
                        sample_rate=16000,
                    )
                except Exception as e:
                    log.error(f"sender: ws.transcribe() raised on chunk {sent}: {e!r}")
                    raise
                dt = time.time() - t_pre
                if not first_send_logged:
                    log.info(f"sender: chunk 1 acknowledged by SDK in {dt*1000:.0f}ms")
                    first_send_logged = True
                if dt > 0.4:
                    slow_sends += 1
                now = time.time()
                if now - last_report >= 5:
                    total = sent + skipped
                    pct   = (skipped / total * 100) if total else 0
                    log.info(f"sender: sent {sent}, skipped {skipped} silent "
                             f"({pct:.0f}% saved) in last {now-last_report:.1f}s "
                             f"(slow_sends={slow_sends}, last_dt={dt*1000:.0f}ms)")
                    sent, skipped, slow_sends, last_report = 0, 0, 0, now
            log.info("sender: audio_gen exhausted / stopped, flushing")
            try:
                await ws.flush()
            except Exception:
                pass

        sender_task = asyncio.create_task(sender())

        # If the sender dies on a NON-recoverable error (e.g. SDK validation
        # bug), surface it and end the session. If it dies because the WS
        # closed (ConnectionClosed), the outer reconnect loop will handle it
        # — don't set stop_event for that.
        def _on_sender_done(task):
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            if isinstance(exc, _wse.ConnectionClosed):
                log.warning(f"sender task: WS closed ({type(exc).__name__}) "
                            "— reconnect loop will handle")
                return
            log.error(f"sender task died with unrecoverable error: {exc!r} — stopping session")
            stop_event.set()
        sender_task.add_done_callback(_on_sender_done)

        msg_counts: dict[str, int] = {}
        try:
            async for msg in ws:
                if stop_event.is_set():
                    break

                if isinstance(msg, dict):
                    d = msg
                else:
                    d = {k: v for k, v in vars(msg).items() if not k.startswith("_")} if hasattr(msg, "__dict__") else {}
                    if hasattr(msg, "model_dump"):
                        d = msg.model_dump()

                # Sarvam Saaras v3 envelopes:
                #   {"type":"data",   "data":{"transcript":"...", "metrics":{...}}}
                #     → one final translated utterance.
                #   {"type":"events", "data":{"signal_type":"START_SPEECH"|"END_SPEECH", ...}}
                #     → VAD boundaries.
                envelope = d.get("type", "")
                inner = d.get("data") if isinstance(d.get("data"), dict) else {}

                if envelope == "data":
                    text = (inner.get("transcript") or inner.get("text") or "").strip()
                    msg_counts["transcript"] = msg_counts.get("transcript", 0) + 1
                    if text:
                        dur = (inner.get("metrics") or {}).get("audio_duration")
                        suffix = f"  [{dur:.2f}s]" if isinstance(dur, (int, float)) else ""
                        log.info(f"FINAL  ▶ {text}{suffix}")
                        # ── Multi-target fan-out ─────────────────────────────
                        # The display wants `target`; each enabled feed wants
                        # its own target_lang. Saaras returned text in
                        # `saaras_out`. For every UNIQUE wanted language we
                        # need a Mayura call (skipping the one that matches
                        # saaras_out — that's a free passthrough).
                        registry = state.get("feed_registry") if state else None
                        feed_targets: set[str] = set()
                        if registry is not None:
                            for f in registry.enabled():
                                if f.target_lang in SARVAM_LANG_CODES:
                                    feed_targets.add(f.target_lang)
                        wanted = {target} | feed_targets
                        wanted.discard(saaras_out)   # saaras_out is free

                        # Parallel Mayura calls — limit to ≤ 8 concurrent to
                        # avoid hammering the API on rare wide fan-outs.
                        translated: dict[str, str] = {saaras_out: text}
                        if wanted and not mt_disabled:
                            t_mt = time.time()
                            async def _do_one(tgt: str) -> tuple[str, str]:
                                out = await _mayura_translate(
                                    mt_session, api_key, text,
                                    source_lang=saaras_out, target_lang=tgt,
                                    model=mayura_model_for(saaras_out, tgt),
                                )
                                return tgt, out
                            results = await asyncio.gather(
                                *( _do_one(t) for t in wanted ),
                                return_exceptions=False,
                            )
                            for tgt, out in results:
                                translated[tgt] = out
                            mt_ms = (time.time() - t_mt) * 1000
                            log.info(f"Mayura fan-out → {sorted(wanted)} in {mt_ms:.0f}ms total")
                        elif wanted:
                            # No Mayura available — substitute source text so
                            # downstream still gets something.
                            for t in wanted: translated[t] = text

                        # ── Post-translate rule pass ─────────────────────────
                        # Apply substitution rules to each target's output
                        # before any downstream surface sees it. Rules are
                        # whole-word case-insensitive by default; longer
                        # phrases beat shorter ones; exclusion (replacement
                        # == "…") masks a word without dropping the
                        # utterance. The same rule set runs against every
                        # target so the LED wall, PP, YouTube CC tracks, and
                        # Pi displays stay consistent.
                        #
                        # We keep `raw_by_target` alongside the translated
                        # dict so the operator UI can show a badge with the
                        # pre-rules text. `translated[target]` gets
                        # overwritten to the corrected text (that's what
                        # downstream surfaces use).
                        raw_by_target: dict[str, str] = dict(translated)
                        rules_reg: "RulesRegistry | None" = state.get("rules_registry") if state else None
                        active_rules = rules_reg.all() if rules_reg is not None else []
                        fired_by_target: dict[str, list[str]] = {}
                        for tgt_lang in list(translated.keys()):
                            corrected, fired = apply_rules(translated[tgt_lang], active_rules)
                            translated[tgt_lang]     = corrected
                            fired_by_target[tgt_lang] = fired

                        display_corrected = translated.get(target, text)
                        display_raw       = raw_by_target.get(target, text)
                        display_fired     = fired_by_target.get(target, [])
                        fired_labels      = [rules_reg.label_for(rid) for rid in display_fired] if rules_reg else []

                        await broadcaster.send({
                            "type":        "final",
                            "text":        display_corrected,
                            "raw":         display_raw,
                            "rules_fired": fired_labels,
                            "target_lang": yt_lang_from_sarvam(target),
                        })

                        # Per-feed routing: each enabled feed gets its own
                        # target's text. FINALs only — partials are
                        # LED-wall-only by design.
                        if registry is not None:
                            for f in registry.enabled():
                                feed_text = translated.get(f.target_lang, text)
                                f.pusher.submit(feed_text)

                        if pp_session and pp_id:
                            await push_to_pp(pp_session, pp_id, display_corrected)

                        # Storage: record raw + corrected + which rules
                        # fired so the SRT export and forensic audit can
                        # use whichever text they need.
                        recorder: "SessionRecorder | None" = state.get("session_recorder") if state else None
                        if recorder is not None and recorder.is_active():
                            recorder.write_final(
                                raw          = display_raw,
                                corrected    = display_corrected,
                                rules_fired  = display_fired,
                                source_lang  = source,
                                target_lang  = target,
                                audio_level  = state.get("last_audio_level") if state else None,
                            )
                    else:
                        log.info(f"sarvam: empty data msg — inner={inner}")
                elif envelope == "events":
                    sig = inner.get("signal_type") or inner.get("event_type") or "?"
                    msg_counts[f"event:{sig}"] = msg_counts.get(f"event:{sig}", 0) + 1
                    if sig == "START_SPEECH":
                        log.info("sarvam: VAD start")
                        await broadcaster.send({"type": "partial", "text": "…"})
                    elif sig == "END_SPEECH":
                        log.info("sarvam: VAD end")
                        await broadcaster.send({"type": "partial", "text": ""})
                    else:
                        log.info(f"sarvam: event signal_type={sig!r} inner={inner}")
                elif envelope == "error":
                    log.error(f"sarvam ERROR: {d}")
                else:
                    msg_counts[envelope] = msg_counts.get(envelope, 0) + 1
                    log.info(f"sarvam: unknown envelope={envelope!r} d={d}")
        finally:
            # Always tidy up the sender on session exit (clean close or raise).
            if not sender_task.done():
                sender_task.cancel()
                try:
                    await sender_task
                except (asyncio.CancelledError, Exception):
                    pass

        if msg_counts:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(msg_counts.items()))
            log.info(f"sarvam session summary: {summary}")
        else:
            log.warning("sarvam session summary: NO messages received from Sarvam")


# ── HTTP handlers ─────────────────────────────────────────────────────────────

async def handle_config(request: web.Request):
    """Single-shot boot config for the React UI. Branding + defaults +
    language matrix in one round-trip. Fetched once on app mount; the
    response is small enough that no caching is needed."""
    return web.json_response({
        "appName":       APP_NAME,
        "accentHsl":     ACCENT_HSL,
        "defaultSource": DEFAULT_SOURCE_LANG,
        "defaultTarget": DEFAULT_TARGET_LANG,
        "sarvamLangs":   [list(p) for p in SARVAM_LANGS],
        "mayuraLangs":   sorted(MAYURA_LANGS),
    })


async def handle_devices(request: web.Request):
    devices = list_audio_devices()
    return web.json_response({"devices": devices})


async def _stop_monitor(app) -> None:
    """Stop the always-on level monitor and wait for it to release the
    device. Idempotent — does nothing if no monitor is running."""
    state = app["state"]
    ev   = state.get("monitor_stop_event")
    task = state.get("monitor_task")
    if ev: ev.set()
    if task and not task.done():
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            task.cancel()
            try: await task
            except Exception: pass
    state["monitor_task"] = None
    state["monitor_stop_event"] = None


async def handle_monitor_start(request: web.Request):
    """Open the given input device and start broadcasting level events. The
    operator's audio meter pulls from these so they can verify the mic is
    hot before pressing Start.
    """
    app = request.app
    state = app["state"]
    if state.get("caption_task") and not state["caption_task"].done():
        return web.json_response({"ok": False, "reason": "transcribing"}, status=409)
    body = await request.json()
    device = body.get("device")
    if device in (None, ""):
        return web.Response(status=400, text="device required")
    # Stop any existing monitor first (device change, etc.)
    await _stop_monitor(app)
    stop_event = asyncio.Event()
    state["monitor_stop_event"] = stop_event
    state["monitor_task"] = asyncio.create_task(
        audio_monitor_loop(device, app["broadcaster"], stop_event)
    )
    return web.json_response({"ok": True})


async def handle_monitor_stop(request: web.Request):
    await _stop_monitor(request.app)
    return web.json_response({"ok": True})


async def handle_start(request: web.Request):
    app = request.app
    state = app["state"]
    if state.get("caption_task") and not state["caption_task"].done():
        return web.Response(status=409, text="Already running")

    body = await request.json()
    source  = body.get("source", "device")   # audio source: device | file
    device  = body.get("device")
    file    = body.get("file")
    seconds = body.get("seconds")
    sarvam_cfg = body.get("sarvam") or {}
    gate_cfg   = body.get("gate")   or {}
    # Language pair — keyed `source_lang` / `target_lang` on the wire to
    # avoid colliding with the audio-`source` field above. If the JS posted
    # explicit values, prefer them; otherwise keep whatever was last set.
    src_lang = body.get("source_lang")
    tgt_lang = body.get("target_lang")
    if src_lang in SARVAM_LANG_CODES:
        state["source"] = src_lang
    if tgt_lang in SARVAM_LANG_CODES:
        state["target"] = tgt_lang
    state.setdefault("source", DEFAULT_SOURCE_LANG)
    state.setdefault("target", DEFAULT_TARGET_LANG)

    # Release the level monitor first so the device is available for the
    # real capture in sarvam_loop. CoreAudio gives exclusive access on macOS.
    await _stop_monitor(app)

    broadcaster: Broadcaster = app["broadcaster"]
    use_pp: bool = app["use_pp"]

    if source == "file":
        if not file or not Path(file).exists():
            return web.Response(status=400, text=f"File not found: {file}")
        audio_gen = audio_from_file(file, seconds)
    else:
        audio_gen = audio_from_device(device)

    stop_event = asyncio.Event()
    state["stop_event"] = stop_event
    # Remember audio_source + device so a mid-session page refresh can
    # re-sync the operator UI (dropdown selection, mic/file radio). Without
    # this the refresh defaults the dropdown to the first device and the
    # operator thinks the wrong mic is in use. NB: `audio_source` is the
    # mic/file selector — NOT to be confused with `state["source"]` which
    # holds the *language* source code (gu-IN, en-IN, …).
    state["audio_source"] = source
    state["device"]       = device
    state["file"]         = file

    # Open the per-session JSONL recorder. Header captures the config the
    # operator chose so a forensic look-back can reconstruct the pipeline
    # state (audio device, language pair, Sarvam knobs, gate thresholds).
    recorder: SessionRecorder | None = app.get("session_recorder")
    if recorder is not None:
        recorder.start({
            "audio_source": source,
            "device":       device,
            "file":         file,
            "seconds":      seconds,
            "source_lang":  state["source"],
            "target_lang":  state["target"],
            "sarvam":       sarvam_cfg,
            "gate":         gate_cfg,
        })

    state["caption_task"] = asyncio.create_task(
        sarvam_loop(audio_gen, broadcaster, use_pp, stop_event, state,
                    sarvam_cfg=sarvam_cfg, gate_cfg=gate_cfg)
    )
    log.info(f"Started: audio_source={source} device={device} file={file} seconds={seconds} "
             f"direction={state['source']}→{state['target']} sarvam={sarvam_cfg} gate={gate_cfg}")
    # Broadcast running=true so every connected tab (including overlays
    # opened elsewhere on the LAN) flips its Start→Stop state without
    # waiting for the operator to refresh. Mirrors the handle_ws snapshot
    # shape so clients can reuse the same handler.
    try:
        await broadcaster.send({
            "type":         "session_status",
            "running":      True,
            "lang_source":  state["source"],
            "lang_target":  state["target"],
            "audio_source": source,
            "device":       device,
            "file":         file,
        })
    except Exception:
        pass
    return web.json_response({
        "status": "started",
        "source": state["source"],
        "target": state["target"],
    })


async def handle_direction(request: web.Request):
    """POST /api/direction {source: "<lang>", target: "<lang>"}.

    Mutates the server-side language pair. If a session is running, closes
    the live Sarvam WS so the reconnect loop picks up the new mode + lang.
    Idle case: just persists; the next /api/start uses it.

    YouTube CC fan-out is driven from the FINAL handler (per-feed target_lang
    via FeedRegistry), so this handler does NOT have to cascade lang values
    to feeds — they each carry their own.
    """
    app = request.app
    state = app["state"]
    body = await request.json()
    src = body.get("source")
    tgt = body.get("target")
    if src not in SARVAM_LANG_CODES or tgt not in SARVAM_LANG_CODES:
        return web.Response(status=400,
                            text=f"source and target must each be one of "
                                 f"{sorted(SARVAM_LANG_CODES)} — got src={src!r}, tgt={tgt!r}")
    prev_src = state.get("source", DEFAULT_SOURCE_LANG)
    prev_tgt = state.get("target", DEFAULT_TARGET_LANG)
    state["source"] = src
    state["target"] = tgt
    # If a session is live, close the current Sarvam WS to trigger reconnect
    # with the new direction. audio_gen, browser tabs, transcript, PP id all
    # survive. The SDK wrapper has no .close() — close the underlying ws.
    ws_client = state.get("sarvam_ws")
    if ws_client is not None:
        try:
            inner = getattr(ws_client, "_websocket", None)
            if inner is None:
                raise AttributeError(f"no _websocket on {type(ws_client).__name__}")
            await inner.close()
            log.info(f"flip: {prev_src}→{prev_tgt}  ⇒  {src}→{tgt}  (closed live Sarvam WS)")
        except Exception as e:
            log.warning(f"flip: ws.close raised {e!r}")
    else:
        log.info(f"flip: {prev_src}→{prev_tgt}  ⇒  {src}→{tgt}  (no live session — applied to state only)")
    # Broadcast the new lang pair to every connected client so the
    # operator's other tabs and the overlay all reflect the flip. Same
    # message shape the WS-connect snapshot uses, so the React handler
    # can reuse its existing session_status handler.
    broadcaster: Broadcaster = app["broadcaster"]
    task   = state.get("caption_task")
    is_run = bool(task and not task.done())
    try:
        await broadcaster.send({
            "type":         "session_status",
            "running":      is_run,
            "lang_source":  src,
            "lang_target":  tgt,
            "audio_source": state.get("audio_source"),
            "device":       state.get("device"),
            "file":         state.get("file"),
        })
    except Exception:
        pass
    return web.json_response({
        "ok": True, "source": src, "target": tgt,
        "previous_source": prev_src, "previous_target": prev_tgt,
    })


async def handle_stop(request: web.Request):
    state = request.app["state"]
    if ev := state.get("stop_event"):
        ev.set()
    if task := state.get("caption_task"):
        task.cancel()
    return web.json_response({"status": "stopped"})


# ── mDNS service advertisement ────────────────────────────────────────────────
#
# Caption sidecars (e.g. Raspberry Pi displays) use this to auto-discover the
# captions tool wherever it's running on the local network — operator can
# shift the tool to a laptop or a different Mac and clients re-connect without
# config.
#
# Service type: _captions._tcp.local. (custom, no IANA reg — local-link only)
# Properties:   path=/ws (WebSocket endpoint), version=1 (protocol version)

CAPTIONS_SERVICE_TYPE = "_captions._tcp.local."

def _detect_local_ip() -> str:
    """Best-effort local IP detection. Uses the UDP-connect trick so the
    kernel picks the interface that would route to a public address (no actual
    packets are sent). Falls back to 127.0.0.1 if everything fails."""
    import socket as _sock
    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    try:
        # Connect to a public IP; the kernel binds the socket to whichever
        # local interface routes outbound, which gives us the LAN-facing IP.
        s.connect(("8.8.8.8", 1))
        return s.getsockname()[0]
    except Exception:
        try:
            return _sock.gethostbyname(_sock.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        try: s.close()
        except Exception: pass


async def register_mdns_service(port: int) -> tuple[object, object] | None:
    """Register the captions tool as `_captions._tcp.local.` on mDNS.
    Returns (zc, info) so main() can unregister on shutdown. Returns None
    if zeroconf isn't importable (graceful degradation — Pi sidecars can
    still connect via an explicit CAPTIONS_WS_URL env override)."""
    try:
        from zeroconf.asyncio import AsyncZeroconf
        from zeroconf import ServiceInfo
    except ImportError as e:
        log.warning(f"mDNS: zeroconf not available ({e}); Pi sidecars will need "
                    f"CAPTIONS_WS_URL set explicitly")
        return None
    import socket as _sock
    ip = _detect_local_ip()
    hostname = _sock.gethostname().split(".")[0]
    # Service instance name must be unique on the LAN. Use the hostname so
    # if two captions tools come up at once (rare — usually only one) they
    # don't collide.
    instance = f"captions-{hostname}"
    info = ServiceInfo(
        type_=CAPTIONS_SERVICE_TYPE,
        name=f"{instance}.{CAPTIONS_SERVICE_TYPE}",
        addresses=[_sock.inet_aton(ip)],
        port=port,
        properties={
            b"path":    b"/ws",
            b"version": b"1",
        },
        server=f"{hostname}.local.",
    )
    zc = AsyncZeroconf()
    try:
        await zc.async_register_service(info)
    except Exception as e:
        log.warning(f"mDNS: registration failed ({e}); Pi sidecars will need "
                    f"CAPTIONS_WS_URL set explicitly")
        await zc.async_close()
        return None
    log.info(f"mDNS: advertising as '{instance}' at {ip}:{port} "
             f"(type {CAPTIONS_SERVICE_TYPE})")
    return zc, info


async def unregister_mdns_service(handle) -> None:
    if handle is None:
        return
    zc, info = handle
    try:
        await zc.async_unregister_service(info)
    except Exception:
        pass
    try:
        await zc.async_close()
    except Exception:
        pass


async def handle_ws(request: web.Request):
    broadcaster: Broadcaster = request.app["broadcaster"]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    broadcaster.add(ws)
    log.info(f"WS connected ({len(broadcaster._clients)} clients)")
    # Snapshot the YT pusher state to the freshly-connected tab so the toggle
    # / delay / status pill reflect current values without waiting for the
    # next periodic broadcast.
    pusher: YouTubeCaptionPusher | None = request.app.get("yt_pusher")
    if pusher is not None:
        try:
            await ws.send_str(json.dumps({"type": "yt_status", **pusher.status()}))
        except Exception:
            pass
    # Snapshot session state so a tab that refreshed mid-session paints
    # the right button (■ Stop, not ▶ Start), the right direction pill,
    # and the right input device. Without this, the browser would let the
    # operator click Start and get a 409 "Already running", and the device
    # dropdown would default to the first option rather than the one
    # actually in use.
    state = request.app["state"]
    task   = state.get("caption_task")
    is_run = bool(task and not task.done())
    try:
        await ws.send_str(json.dumps({
            "type":         "session_status",
            "running":      is_run,
            "lang_source":  state.get("source", DEFAULT_SOURCE_LANG),
            "lang_target":  state.get("target", DEFAULT_TARGET_LANG),
            "audio_source": state.get("audio_source"),
            "device":       state.get("device"),
            "file":         state.get("file"),
        }))
    except Exception:
        pass
    # Snapshot the current YouTube CC feeds so a fresh tab paints the
    # Outputs list without having to trigger a write first. Mirrors the
    # rules + vod_jobs snapshots below.
    feed_reg: FeedRegistry | None = request.app.get("feed_registry")
    if feed_reg is not None:
        try:
            await ws.send_str(json.dumps({
                "type":  "feeds_list",
                "feeds": feed_reg.list_for_wire(),
            }))
        except Exception:
            pass
    # Snapshot the current rules list so a fresh tab paints the Rules
    # sidebar without waiting for the next mutation broadcast.
    rules_reg: RulesRegistry | None = request.app.get("rules_registry")
    if rules_reg is not None:
        try:
            await ws.send_str(json.dumps({
                "type":  "rules_list",
                "rules": rules_reg.list_for_wire(),
            }))
        except Exception:
            pass
    # VOD jobs snapshot — same rationale, paints the Reprocess tab.
    vod_jobs_reg: VodJobRegistry | None = request.app.get("vod_jobs")
    if vod_jobs_reg is not None:
        try:
            await ws.send_str(json.dumps({
                "type": "vod_jobs_list",
                "jobs": vod_jobs_reg.list_for_wire(),
            }))
        except Exception:
            pass
    # Debug log replay so the Debug panel paints with history instead of
    # waiting for the next log event. After this, live records fan out
    # via _RingHandler.emit → broadcaster.send({type:"log", ...}).
    try:
        await ws.send_str(json.dumps({
            "type": "log_snapshot",
            "logs": list(_recent_logs),
        }))
    except Exception:
        pass
    try:
        async for _ in ws:
            pass
    finally:
        broadcaster.remove(ws)
    return ws


async def handle_feeds_list(request: web.Request):
    """GET /api/feeds → {feeds: [...]} — each feed's wire-safe status."""
    reg: FeedRegistry = request.app["feed_registry"]
    return web.json_response({"feeds": reg.list_for_wire()})


async def handle_feeds_create_or_update(request: web.Request):
    """POST /api/feeds. If body has `id` matching an existing feed → update.
    Else → create. Stream key updates land BEFORE the enabled flip so the
    pusher is configured first.
    """
    reg: FeedRegistry = request.app["feed_registry"]
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad JSON")
    fid = body.get("id")
    # Validation
    target = body.get("target_lang")
    if target is not None and target not in SARVAM_LANG_CODES:
        return web.Response(status=400,
                            text=f"target_lang must be one of {sorted(SARVAM_LANG_CODES)}")
    if fid and fid in reg.feeds:
        f = await reg.update(
            fid,
            label       = body.get("label"),
            stream_key  = body.get("stream_key"),
            target_lang = target,
            enabled     = body.get("enabled")     if "enabled"     in body else None,
            advance_sec = body.get("advance_sec") if "advance_sec" in body else None,
        )
        return web.json_response({"ok": True, "feed": f.status_payload() if f else None})
    # Create
    stream_key = (body.get("stream_key") or "").strip()
    if not stream_key:
        return web.Response(status=400, text="stream_key required when creating a feed")
    if not target:
        target = DEFAULT_TARGET_LANG
    f = await reg.create(
        label       = body.get("label") or "(unnamed)",
        stream_key  = stream_key,
        target_lang = target,
        enabled     = bool(body.get("enabled", False)),
        advance_sec = float(body.get("advance_sec", 1.5)),
    )
    return web.json_response({"ok": True, "feed": f.status_payload()})


async def handle_feeds_delete(request: web.Request):
    """DELETE /api/feeds/<id> — permanently removes the feed (incl. its
    stream key). Operator can re-create by pasting the key again.
    """
    reg: FeedRegistry = request.app["feed_registry"]
    fid = request.match_info["fid"]
    ok = await reg.delete(fid)
    if not ok:
        return web.Response(status=404, text="no such feed")
    return web.json_response({"ok": True, "deleted": fid})


async def handle_feeds_enable(request: web.Request):
    """POST /api/feeds/<id>/enable — light-weight surface for external tools
    (Companion, curl, hotkeys). No body needed. Returns the feed snapshot."""
    reg: FeedRegistry = request.app["feed_registry"]
    fid = request.match_info["fid"]
    f = await reg.update(fid, enabled=True)
    if not f:
        return web.Response(status=404, text="no such feed")
    return web.json_response({"ok": True, "feed": f.status_payload()})


async def handle_feeds_disable(request: web.Request):
    """POST /api/feeds/<id>/disable — light-weight surface (see /enable)."""
    reg: FeedRegistry = request.app["feed_registry"]
    fid = request.match_info["fid"]
    f = await reg.update(fid, enabled=False)
    if not f:
        return web.Response(status=404, text="no such feed")
    return web.json_response({"ok": True, "feed": f.status_payload()})


async def handle_feeds_disable_all(request: web.Request):
    """POST /api/feeds/disable-all — kill-switch. Mutes every enabled feed
    in one call. Used by the sidebar "Disable all" button and as a panic
    button for ops (e.g. fire it from a Companion preset to hush all YT
    output during a service-wide announcement)."""
    reg: FeedRegistry = request.app["feed_registry"]
    n = 0
    for fid, f in list(reg.feeds.items()):
        if f.enabled:
            await reg.update(fid, enabled=False)
            n += 1
    return web.json_response({"ok": True, "disabled": n})


async def handle_feeds_enable_all(request: web.Request):
    """POST /api/feeds/enable-all — the opposite of disable-all. Skips
    feeds with no stream key (those can never go live)."""
    reg: FeedRegistry = request.app["feed_registry"]
    n = 0
    for fid, f in list(reg.feeds.items()):
        if (not f.enabled) and f.pusher and f.pusher.configured:
            await reg.update(fid, enabled=True)
            n += 1
    return web.json_response({"ok": True, "enabled": n})


# ── Rules REST endpoints ─────────────────────────────────────────────────────

async def handle_rules_list(request: web.Request):
    """GET /api/rules → {rules: [...]} — wire-safe rule snapshot."""
    reg: RulesRegistry = request.app["rules_registry"]
    return web.json_response({"rules": reg.list_for_wire()})


async def handle_rules_create_or_update(request: web.Request):
    """POST /api/rules. If body has `id` matching an existing rule → update.
    Else → create. Pattern + replacement are both required for creation
    (replacement may be the empty string only if `is_exclusion` is true,
    in which case it's normalised to "…")."""
    reg: RulesRegistry = request.app["rules_registry"]
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad JSON")
    rid = body.get("id")
    pattern     = body.get("pattern")
    replacement = body.get("replacement")
    if replacement is None and bool(body.get("is_exclusion")):
        replacement = "…"
    if rid and rid in reg.rules:
        r = await reg.update(
            rid,
            pattern     = pattern,
            replacement = replacement,
            regex       = body.get("regex")   if "regex"   in body else None,
            enabled     = body.get("enabled") if "enabled" in body else None,
        )
        return web.json_response({"ok": True, "rule": r.to_wire() if r else None})
    # Create
    if not pattern or not pattern.strip():
        return web.Response(status=400, text="pattern required when creating a rule")
    if replacement is None:
        return web.Response(status=400, text="replacement required (use \"…\" for exclusions)")
    r = await reg.create(
        pattern     = pattern,
        replacement = replacement,
        regex       = bool(body.get("regex", False)),
        enabled     = bool(body.get("enabled", True)),
    )
    return web.json_response({"ok": True, "rule": r.to_wire()})


async def handle_rules_delete(request: web.Request):
    """DELETE /api/rules/<id> — permanent removal."""
    reg: RulesRegistry = request.app["rules_registry"]
    rid = request.match_info["rid"]
    ok = await reg.delete(rid)
    if not ok:
        return web.Response(status=404, text="no such rule")
    return web.json_response({"ok": True, "deleted": rid})


# ── VOD jobs REST endpoints ──────────────────────────────────────────────────

async def handle_vod_jobs_list(request: web.Request):
    """GET /api/vod-jobs → {jobs: [...]}. Newest first."""
    reg: VodJobRegistry = request.app["vod_jobs"]
    return web.json_response({"jobs": reg.list_for_wire()})


async def handle_vod_jobs_create(request: web.Request):
    """POST /api/vod-jobs {video_url: "..."} → enqueue, returns the new job.
    Bad URL → 400 with the parse error."""
    reg: VodJobRegistry = request.app["vod_jobs"]
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="bad JSON")
    url = (body.get("video_url") or "").strip()
    if not url:
        return web.Response(status=400, text="video_url required")
    try:
        job = await reg.create(url)
    except ValueError as e:
        return web.Response(status=400, text=str(e))
    return web.json_response({"ok": True, "job": job.to_wire()})


async def handle_vod_jobs_cancel(request: web.Request):
    """POST /api/vod-jobs/<id>/cancel — flips status to cancelled. If the
    job is currently running, the pipeline task gets cancelled too."""
    reg: VodJobRegistry = request.app["vod_jobs"]
    jid = request.match_info["jid"]
    ok = await reg.cancel(jid)
    if not ok:
        return web.Response(status=404, text="no such job (or not cancellable)")
    return web.json_response({"ok": True, "id": jid})


async def handle_vod_jobs_transcribe(request: web.Request):
    """POST /api/vod-jobs/<id>/transcribe {ranges: [{start_s, end_s}, ...]}
    Resumes a paused (awaiting_ranges) job with operator-picked ranges.
    Empty list = transcribe the full video.
    """
    reg: VodJobRegistry = request.app["vod_jobs"]
    jid = request.match_info["jid"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw_ranges = body.get("ranges") or []
    norm: list[tuple[float, float]] = []
    for r in raw_ranges:
        try:
            s = float(r["start_s"])
            e = float(r["end_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            norm.append((s, e))
    ok = await reg.resume_with_ranges(jid, norm)
    if not ok:
        return web.Response(status=404, text="no such job (or not awaiting ranges)")
    return web.json_response({"ok": True, "id": jid, "ranges": norm})


async def handle_vod_jobs_delete(request: web.Request):
    """DELETE /api/vod-jobs/<id> — remove from history. Doesn't delete the
    files on disk."""
    reg: VodJobRegistry = request.app["vod_jobs"]
    jid = request.match_info["jid"]
    ok = await reg.delete(jid)
    if not ok:
        return web.Response(status=404, text="no such job (or running)")
    return web.json_response({"ok": True, "deleted": jid})


# ── Past-sessions browser ────────────────────────────────────────────────────
#
# captions/results/ holds one JSONL + one SRT per Start→Stop session
# (written by SessionRecorder). These endpoints expose them to the
# operator UI's Transcript tab so historical sessions are browsable
# without leaving the app.

def _session_id_from_path(p: Path) -> str:
    """Filename without extension acts as the stable session id."""
    return p.stem


def _scan_sessions(results_dir: Path, active_path: Path | None) -> list[dict]:
    """Walk results/, parse each JSONL's header + footer for metadata.
    Returns newest-first list. Reading just the first + last lines keeps
    this fast even for hour-long sessions.
    """
    if not results_dir.exists():
        return []
    out: list[dict] = []
    for jsonl in sorted(results_dir.glob("*.jsonl"), reverse=True):
        # Defensively skip VOD-reprocess words caches that live alongside.
        if "-words" in jsonl.stem:
            continue
        try:
            meta = _read_session_metadata(jsonl)
        except Exception as e:
            log.warning(f"_scan_sessions: skipping {jsonl.name}: {e!r}")
            continue
        # A real SessionRecorder file always has a session_start header
        # with an ISO timestamp. Anything else (orphan sample JSONLs,
        # ad-hoc test files) gets filtered out.
        if not meta.get("started_at"):
            continue
        sid = _session_id_from_path(jsonl)
        srt = jsonl.with_suffix(".srt")
        meta.update({
            "id":         sid,
            "jsonl_url":  f"/results/{jsonl.name}",
            "srt_url":    f"/results/{srt.name}" if srt.exists() else None,
            "active":     active_path is not None and active_path == jsonl,
            "size_bytes": jsonl.stat().st_size,
        })
        out.append(meta)
    return out


def _read_session_metadata(jsonl: Path) -> dict:
    """Pull session_start (first line) + session_stop (last line). Falls
    back gracefully when the session is still in progress (no stop record).
    """
    header: dict = {}
    footer: dict = {}
    # First line — session_start
    with jsonl.open("r", encoding="utf-8") as f:
        first = f.readline().strip()
        if first:
            try:
                rec = json.loads(first)
                if rec.get("type") == "session_start":
                    header = rec
            except Exception:
                pass
    # Last non-empty line — could be session_stop, or a partial line if
    # we're reading an in-progress session.
    try:
        with jsonl.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            chunk = b""
            # Read backwards in 4 KB chunks until we find the last
            # newline-terminated line. Bounded so we don't read the
            # entire file for sessions with a long body.
            for back in range(1, 32):
                step = min(4096 * back, end)
                f.seek(max(0, end - step))
                chunk = f.read(step)
                if chunk.count(b"\n") >= 2:
                    break
            lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
            if lines:
                try:
                    rec = json.loads(lines[-1])
                    if rec.get("type") == "session_stop":
                        footer = rec
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "started_at":  header.get("ts", ""),
        "ended_at":    footer.get("ts", ""),
        "source_lang": header.get("source_lang", ""),
        "target_lang": header.get("target_lang", ""),
        "audio_source": header.get("audio_source", ""),
        "device":      header.get("device"),
        "file":        header.get("file"),
        "final_count": footer.get("final_count", 0),
        "elapsed_s":   footer.get("elapsed_s", 0.0),
    }


def _parse_session_finals(jsonl: Path) -> list[dict]:
    """Parse a session's JSONL into a wire-friendly finals[] list. Keeps
    rendering parity with the live in-memory transcript (same fields).
    """
    out: list[dict] = []
    with jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "final":
                continue
            out.append({
                "ts":          rec.get("ts", ""),
                "elapsed_s":   rec.get("elapsed_s", 0.0),
                "raw":         rec.get("raw", ""),
                "text":        rec.get("corrected") or rec.get("raw") or "",
                "rules_fired": rec.get("rules_fired") or [],
                "audio_level": rec.get("audio_level"),
            })
    return out


async def handle_sessions_list(request: web.Request):
    """GET /api/sessions → {sessions: [...]} newest first.
    Includes the currently-active session as `active: true` if any.
    """
    recorder: SessionRecorder = request.app["session_recorder"]
    results_dir = recorder.results_dir
    active_path = recorder.path if recorder.is_active() else None
    sessions = _scan_sessions(results_dir, active_path)
    return web.json_response({"sessions": sessions})


async def handle_session_get(request: web.Request):
    """GET /api/sessions/<id> → full session: header metadata + finals[]."""
    recorder: SessionRecorder = request.app["session_recorder"]
    results_dir = recorder.results_dir
    sid = request.match_info["sid"]
    # Resolve id → path; reject anything that tries to escape the dir.
    jsonl = results_dir / f"{sid}.jsonl"
    try:
        jsonl_resolved = jsonl.resolve()
        results_resolved = results_dir.resolve()
        if not str(jsonl_resolved).startswith(str(results_resolved) + os.sep) and jsonl_resolved.parent != results_resolved:
            return web.Response(status=400, text="bad session id")
    except Exception:
        return web.Response(status=400, text="bad session id")
    if not jsonl.exists():
        return web.Response(status=404, text="no such session")
    active_path = recorder.path if recorder.is_active() else None
    meta = _read_session_metadata(jsonl)
    meta["id"] = sid
    meta["active"] = active_path is not None and active_path == jsonl
    meta["jsonl_url"] = f"/results/{jsonl.name}"
    srt = jsonl.with_suffix(".srt")
    meta["srt_url"] = f"/results/{srt.name}" if srt.exists() else None
    finals = _parse_session_finals(jsonl)
    return web.json_response({"session": meta, "finals": finals})


async def handle_session_delete(request: web.Request):
    """DELETE /api/sessions/<id> → remove JSONL + SRT from disk. Refuses
    to delete the active session (operator must stop first).
    """
    recorder: SessionRecorder = request.app["session_recorder"]
    results_dir = recorder.results_dir
    sid = request.match_info["sid"]
    jsonl = results_dir / f"{sid}.jsonl"
    try:
        jsonl_resolved = jsonl.resolve()
        results_resolved = results_dir.resolve()
        if jsonl_resolved.parent != results_resolved:
            return web.Response(status=400, text="bad session id")
    except Exception:
        return web.Response(status=400, text="bad session id")
    if not jsonl.exists():
        return web.Response(status=404, text="no such session")
    if recorder.is_active() and recorder.path == jsonl:
        return web.Response(status=409, text="cannot delete the active session — stop it first")
    srt = jsonl.with_suffix(".srt")
    try:
        jsonl.unlink()
        if srt.exists(): srt.unlink()
    except Exception as e:
        return web.Response(status=500, text=str(e))
    return web.json_response({"ok": True, "deleted": sid})


async def handle_upload_audio(request: web.Request):
    """POST /api/upload-audio — multipart upload, saves to captions/uploads/,
    returns {path, name, size} so the operator UI can then POST /api/start
    with source=file and the returned absolute path.

    The on-disk filename is timestamped + sanitized to avoid collisions and
    to keep the basename safe to embed in URLs. Files persist after the
    session — re-running with different language settings should not require
    a re-upload.
    """
    uploads_dir: Path = request.app["uploads_dir"]
    uploads_dir.mkdir(parents=True, exist_ok=True)

    reader = await request.multipart()
    field = await reader.next()
    while field is not None and field.name != "file":
        field = await reader.next()
    if field is None:
        return web.Response(status=400, text="missing 'file' part")

    orig = field.filename or "upload"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", orig).strip("._") or "upload"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = uploads_dir / f"{stamp}-{safe}"

    size = 0
    with out_path.open("wb") as f:
        while True:
            chunk = await field.read_chunk(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    log.info(f"upload-audio: saved {size} bytes to {out_path}")
    return web.json_response({
        "ok":   True,
        "path": str(out_path),
        "name": orig,
        "size": size,
    })


async def handle_test_render(request: web.Request):
    """POST /api/test-render — broadcast a synthetic FINAL via the
    existing Broadcaster so every connected client (operator surface
    and overlay served at /?overlay=1) renders the same text.

    Without this, the React Test button could only update the local
    store — meaning the overlay (a separate browser tab with its own
    store) would never see it. Going through the WS path mirrors what
    real Sarvam FINALs do.
    """
    broadcaster: Broadcaster = request.app["broadcaster"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or
            "Testing live caption rendering — this is a server-side test FINAL.")
    await broadcaster.send({
        "type":        "final",
        "text":        text,
        "raw":         text,
        "rules_fired": [],
        "target_lang": "en",
    })
    return web.json_response({"ok": True, "broadcast": text})


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(args):
    broadcaster = Broadcaster()

    # Now that we're inside the running loop, wire the logging ring handler
    # so live log records fan out over the WS bus as they happen.
    global _log_loop, _log_broadcaster
    _log_loop        = asyncio.get_running_loop()
    _log_broadcaster = broadcaster

    # Feed registry — manages a list of YouTube CC destinations, each with
    # its own pusher worker. Migrates from the legacy YOUTUBE_STREAM_KEY env
    # var on first run (writes outputs.json next to .env).
    feeds_path = Path(__file__).parent / "outputs.json"
    feed_registry = FeedRegistry(broadcaster, feeds_path)
    await feed_registry.load()

    # Rules registry — substitution rules applied to translated output
    # before any downstream surface. Seeds from rules.starter.json on
    # first run if rules.json doesn't yet exist.
    rules_path         = Path(__file__).parent / "rules.json"
    rules_starter_path = Path(__file__).parent / "rules.starter.json"
    rules_registry = RulesRegistry(broadcaster, rules_path, rules_starter_path)
    await rules_registry.load()

    # Session recorder — one JSONL + sibling SRT per Start→Stop session.
    results_dir = Path(__file__).parent / "results"
    session_recorder = SessionRecorder(results_dir, APP_NAME)

    # Audio uploads dir — POST /api/upload-audio writes here, the path is
    # then passed to /api/start with source=file. Gitignored.
    uploads_dir = Path(__file__).parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # VOD reprocess job registry — drives tools.vod_pipeline.VodPipeline
    # for the operator UI's Reprocess tab. Persists historical jobs to
    # vod-jobs.json (gitignored).
    vod_jobs_path = Path(__file__).parent / "vod-jobs.json"
    vod_jobs = VodJobRegistry(broadcaster, vod_jobs_path,
                                results_dir=results_dir, rules_path=rules_path)
    await vod_jobs.load()

    # client_max_size raised so /api/upload-audio can take long audio files.
    # Default aiohttp limit is 1 MiB which would reject anything longer than
    # ~30 seconds of typical MP3. 2 GiB covers a multi-hour session.
    app = web.Application(client_max_size=2 * 1024**3)
    app["broadcaster"]      = broadcaster
    app["use_pp"]           = args.propresenter
    app["feed_registry"]    = feed_registry
    app["rules_registry"]   = rules_registry
    app["session_recorder"] = session_recorder
    app["vod_jobs"]         = vod_jobs
    app["uploads_dir"]      = uploads_dir
    # All runtime-mutable fields go in a single nested dict so we mutate the
    # inner dict (which aiohttp doesn't track) rather than the app dict itself
    # — avoids the "Changing state of started or joined application" warning.
    app["state"] = {
        "caption_task":       None,
        "stop_event":         None,
        "monitor_task":       None,
        "monitor_stop_event": None,
        # Language pair — see SARVAM_LANGS + derive_pipeline() at module top.
        # The /api/direction handler mutates source/target and closes the
        # live ws to force a reconnect with the new pipeline.
        "source":             DEFAULT_SOURCE_LANG,
        "target":             DEFAULT_TARGET_LANG,
        "sarvam_ws":          None,
        # FeedRegistry — pulled into state so sarvam_loop's FINAL handler
        # can read enabled() without an aiohttp.app reference.
        "feed_registry":      feed_registry,
        # RulesRegistry — same rationale; FINAL handler applies rules.
        "rules_registry":     rules_registry,
        # SessionRecorder — FINAL handler writes records here; lifecycle
        # owned by handle_start / sarvam_loop's finally block.
        "session_recorder":   session_recorder,
        # Latest audio level (0..1 peak), updated by sarvam_loop's sender so
        # the recorder can stash it on each FINAL record.
        "last_audio_level":   None,
    }

    app.router.add_get("/api/config",      handle_config)
    app.router.add_get("/api/devices",     handle_devices)
    app.router.add_post("/api/start",      handle_start)
    app.router.add_post("/api/stop",       handle_stop)
    app.router.add_post("/api/direction",  handle_direction)
    app.router.add_post("/api/monitor/start", handle_monitor_start)
    app.router.add_post("/api/monitor/stop",  handle_monitor_stop)
    app.router.add_get("/api/feeds",                   handle_feeds_list)
    app.router.add_post("/api/feeds",                  handle_feeds_create_or_update)
    app.router.add_post("/api/feeds/enable-all",       handle_feeds_enable_all)
    app.router.add_post("/api/feeds/disable-all",      handle_feeds_disable_all)
    app.router.add_post("/api/feeds/{fid}/enable",     handle_feeds_enable)
    app.router.add_post("/api/feeds/{fid}/disable",    handle_feeds_disable)
    app.router.add_delete("/api/feeds/{fid}",          handle_feeds_delete)
    app.router.add_get("/api/rules",                   handle_rules_list)
    app.router.add_post("/api/rules",                  handle_rules_create_or_update)
    app.router.add_delete("/api/rules/{rid}",          handle_rules_delete)
    app.router.add_get("/api/vod-jobs",                handle_vod_jobs_list)
    app.router.add_post("/api/vod-jobs",               handle_vod_jobs_create)
    app.router.add_post("/api/vod-jobs/{jid}/cancel",     handle_vod_jobs_cancel)
    app.router.add_post("/api/vod-jobs/{jid}/transcribe", handle_vod_jobs_transcribe)
    app.router.add_delete("/api/vod-jobs/{jid}",          handle_vod_jobs_delete)
    app.router.add_post("/api/test-render",            handle_test_render)
    app.router.add_post("/api/upload-audio",           handle_upload_audio)
    app.router.add_get("/api/sessions",                handle_sessions_list)
    app.router.add_get("/api/sessions/{sid}",          handle_session_get)
    app.router.add_delete("/api/sessions/{sid}",       handle_session_delete)
    app.router.add_get("/ws",              handle_ws)

    # /results/ → results/* (mp4, srt, html previews) for the React UI's
    # video player and download links. Must be registered BEFORE the SPA
    # catch-all below so /results/foo.srt isn't swallowed by index.html.
    app.router.add_static("/results", results_dir, show_index=False)

    # ── React UI mounted at / (SPA) ──────────────────────────────────
    # Static dist is built by `cd web && pnpm build`. Routes are
    # registered LAST so the catch-all `/{tail:.*}` doesn't shadow
    # /api/*, /ws, or /results above.
    web_dist = Path(__file__).parent / "web" / "dist"
    if web_dist.exists():
        async def _spa_root(request: web.Request):
            return web.FileResponse(web_dist / "index.html")
        async def _spa_fallback(request: web.Request):
            # Serve a hashed asset if it exists in dist, otherwise fall
            # back to index.html so client-side routes work. Unknown
            # /api/* and /ws/* paths must 404 instead of returning the
            # SPA shell — otherwise typo'd endpoints look like 200 OK to
            # API consumers (and the React fetch parser chokes on HTML).
            rel = request.match_info.get("tail", "")
            if rel == "api" or rel.startswith("api/") \
                    or rel == "ws" or rel.startswith("ws/"):
                raise web.HTTPNotFound()
            candidate = web_dist / rel
            if candidate.is_file():
                return web.FileResponse(candidate)
            return web.FileResponse(web_dist / "index.html")
        app.router.add_get("/",            _spa_root)
        app.router.add_get("/{tail:.*}",   _spa_fallback)
        log.info(f"React UI: serving {web_dist} at /")
    else:
        async def _spa_missing(request: web.Request):
            return web.Response(
                status=503, content_type="text/html",
                text="<h1>UI not built yet</h1><p>Run "
                     "<code>cd web &amp;&amp; pnpm install &amp;&amp; pnpm build</code> "
                     "and reload.</p>"
            )
        app.router.add_get("/",          _spa_missing)
        app.router.add_get("/{tail:.*}", _spa_missing)
        log.info(f"React UI: dist not built — / returns a placeholder")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", args.port)
    await site.start()

    # Start the VOD jobs worker AFTER the HTTP listener is up so anyone
    # browsing in immediately sees a healthy server.
    vod_jobs.start_worker()

    # mDNS advert AFTER the socket is actually bound — guarantees a sidecar
    # that resolves the service and immediately connects won't race with
    # the listener coming up.
    mdns_handle = await register_mdns_service(args.port)

    log.info(f"─────────────────────────────────────────")
    log.info(f"Operator UI  → http://localhost:{args.port}/")
    log.info(f"Overlay      → http://localhost:{args.port}/?overlay=1")
    log.info(f"Open in browser, choose audio source, click Start")
    log.info(f"─────────────────────────────────────────")
    if args.propresenter:
        log.info(f"ProPresenter push: {PP_HOST}:{PP_PORT}")
    n_feeds = len(feed_registry.feeds)
    if n_feeds:
        log.info(f"YouTube CC: {n_feeds} feed(s) loaded from {feeds_path}")
    else:
        log.info(f"YouTube CC: no feeds yet — add one via the operator UI "
                 f"or paste a key with YOUTUBE_STREAM_KEY in .env then restart")

    try:
        await asyncio.Event().wait()   # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await unregister_mdns_service(mdns_handle)
        await vod_jobs.shutdown()
        await feed_registry.shutdown()
        await runner.cleanup()


def entry():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--propresenter", action="store_true",
                   help="Also push captions to ProPresenter Messages overlay")
    args = p.parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    entry()
