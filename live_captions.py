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
import logging.handlers
import os
import re
import signal
import tempfile
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

LOG_FORMAT      = "%(asctime)s %(levelname)s %(message)s"
LOG_FILENAME    = "live-captions.log"
LOG_RETAIN_DAYS = 14
LOG_DIR_ENV     = "LIVE_CAPTIONS_LOG_DIR"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("captions")

# The file handler, once `configure_logging` has placed one. Module-level so a
# second call replaces it instead of stacking a duplicate — every line landing
# twice is how a log stops being read.
_file_handler: "logging.Handler | None" = None


def _log_dir_candidates(log_dir: "str | None") -> "list[Path]":
    """Where to try putting the log, best first.

    An explicit directory (or the environment) is a decision someone made,
    so it gets one attempt and no second-guessing: silently writing
    somewhere else would be worse than no file. Otherwise we walk a chain,
    because the mandir PC is the machine that will one day be locked down
    with the install directory read-only, and a lost log is exactly what
    issue #57 is about.
    """
    if log_dir:
        return [Path(log_dir)]

    env = os.environ.get(LOG_DIR_ENV)
    if env:
        return [Path(env)]

    # Next to the code, because whoever is diagnosing already has the
    # checkout open. `logs/` is gitignored.
    out = [Path(__file__).resolve().parent / "logs"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "live-captions" / "logs")
    out.append(Path(tempfile.gettempdir()) / "live-captions-logs")
    return out


def configure_logging(log_dir: "str | None" = None) -> "Path | None":
    """Put the log somewhere that survives the window that started it.

    Issue #57: on 3 September the server ran for 98 minutes doing nothing
    and left NO log, because `basicConfig` alone writes to stderr and
    stderr was a console window nobody kept. The cause had to be
    reconstructed from `explorer.exe` start times. Two later outages the
    same night were diagnosed in seconds — from a redirect someone had
    typed by hand, which lived exactly as long as that one launch.

    Returns the path now being written to, or None if nothing was
    writable. Never raises: a katha with captions and no log beats no
    katha, so a logging fault degrades to console-only and says so.
    """
    global _file_handler

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(LOG_FORMAT)

    # basicConfig() at import normally supplies this. Re-adding it when it is
    # missing matters for the embedded cases (a test, a REPL) where the file
    # is the only handler and a silent console is confusing.
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None

    for d in _log_dir_candidates(log_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
            path = d / LOG_FILENAME
            # Rotate at midnight rather than by size: "what happened on the
            # 3rd" is the question that gets asked, and a katha PC is never
            # tidied by hand, so an unbounded file is a disk-full outage
            # waiting a year to happen.
            handler = logging.handlers.TimedRotatingFileHandler(
                path, when="midnight", backupCount=LOG_RETAIN_DAYS,
                encoding="utf-8",
            )
            handler.setFormatter(fmt)
            root.addHandler(handler)
            _file_handler = handler
            return path
        except OSError:
            continue

    return None

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

# ── Sarvam Saaras model list ─────────────────────────────────────────────────
# Every Saaras STT model the settings UI may offer. Defined once, here —
# /api/config hands this same list to the React settings dropdown, and the
# session-start path validates against it, so the server and the UI cannot
# drift apart the way a hardcoded <option> list and a hardcoded server
# default previously could.
#
# Verified against Sarvam's live docs and the installed sarvamai SDK
# (2026-08-31; SpeechToTextStreamingModel = Literal["saaras:v3", "saaras:v4"]
# on the exact `speech_to_text_streaming.connect()` call this app makes):
#   saaras:v3   — current default, still the vendor's recommended model
#   saaras:v4   — current generation ("latest"); adds Global English on top
#                 of the same 22-Indic-language coverage as v3
#
# Two models are deliberately absent, both for the same reason — selecting
# either one fails the connection, and a katha is a live event where an
# operator picking the wrong entry from a dropdown means no captions in
# front of a full hall:
#   saaras:v2   — withdrawn by the vendor.
#   saaras:v2.5 — not withdrawn, but not reachable from here. It exists only
#                 on Sarvam's separate `speech_to_text_translate_streaming`
#                 client, which this app never calls. Offering it labelled
#                 as broken was considered and rejected: a label does not
#                 stop a tired operator at 6am, and there is no case where
#                 choosing it is the right answer.
SARVAM_MODELS: list[dict[str, str]] = [
    {"id": "saaras:v3", "label": "saaras:v3 (default)"},
    {"id": "saaras:v4", "label": "saaras:v4 (latest)"},
]
SARVAM_MODEL_IDS: set[str] = {m["id"] for m in SARVAM_MODELS}
DEFAULT_SARVAM_MODEL = "saaras:v3"

# Per-deployment direction defaults — set in .env so a fresh deploy lands on
# the org's most-common direction without operator setup. This fork's own
# default is pinned to the katha's actual, fixed direction (Gujarati spoken
# → English captions) rather than the generic en-IN/en-IN fallback, so a
# first-ever boot (no .env override, no browser localStorage yet) never
# lands on a guessed/wrong direction — see issue #12.
DEFAULT_SOURCE_LANG = os.environ.get("DEFAULT_SOURCE_LANG", "gu-IN").strip() or "gu-IN"
DEFAULT_TARGET_LANG = os.environ.get("DEFAULT_TARGET_LANG", "en-IN").strip() or "en-IN"
if DEFAULT_SOURCE_LANG not in SARVAM_LANG_CODES:
    DEFAULT_SOURCE_LANG = "en-IN"
if DEFAULT_TARGET_LANG not in SARVAM_LANG_CODES:
    DEFAULT_TARGET_LANG = "en-IN"

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"

# ── Keep-alive through gated silence ─────────────────────────────────────
# Sarvam closes a connection that has received nothing for 60 s. The client
# silence gate (see sarvam_loop) stops sending 1.5 s after the last loud
# audio, so any pause longer than about a minute — a musical item, a reading,
# a break — used to end the session outright. The reconnect that followed was
# the expensive part: its backoff can reach 8 s, so the connection came back
# during the opening words of the next passage, and repeated teardowns across
# one long silence risk the rate limiter refusing to let us back in at all.
#
# A frame of digital silence every SARVAM_KEEPALIVE_SEC stops the teardown
# happening. Digital silence rather than the room noise the gate just
# rejected, so there is still nothing to transcribe and the gate's purpose —
# not paying for an empty room — is untouched.
#
# MEASURED 2026-08-31 against the live endpoint, and it does not match the
# assumption above. Five idle sessions were opened, spoken to briefly, then
# sent nothing: four survived three minutes; one died at 50s with
# "1011 internal error — keepalive ping timeout". There is no reliable 60s
# idle timeout. The one real failure was the WebSocket protocol ping: this
# client pings every 20s (the library default — the vendor SDK passes no ping
# settings), the service does not answer while idle, and OUR side hangs up.
#
# The keep-alive is kept, and defaults ON, for three reasons that survive that
# correction: it is proven harmless (30s of pure digital silence returned zero
# captions, three trials, so it cannot put invented text on a temple screen);
# it costs 14 frames per five-minute pause against a ~600-frame alternative;
# and across 28 hours of live event a rare fault is a certainty. It is
# insurance, NOT a fix for the cause above — honest reconnection is what
# actually covers that. Set SARVAM_KEEPALIVE=off to disable it entirely.
# Named "assumed", not "the" idle timeout, deliberately: the measurement above
# found no reliable timeout at all. This is the pessimistic figure the
# keep-alive interval is sized against, so that the interval stays small
# whatever the true threshold turns out to be. Do not restate it as a vendor
# fact — in a comment, in .env.template, or to an operator.
ASSUMED_IDLE_TIMEOUT_SEC = 60.0
KEEPALIVE_ENABLED = os.environ.get("SARVAM_KEEPALIVE", "on").strip().lower() \
    not in {"0", "off", "false", "no"}


# Half the idle window is the widest interval that still defends it. A
# keep-alive can only leave on an audio frame that has arrived, so the gap
# Sarvam actually sees is the interval plus up to one frame, plus whatever
# the network adds; and an interval of half the window survives losing a beat
# entirely. Anything wider is margin we would only discover we needed live.
MAX_KEEPALIVE_SEC = ASSUMED_IDLE_TIMEOUT_SEC / 2


def _resolve_keepalive_sec(value, fallback: float) -> float:
    """The configured interval if it defends the idle window, else `fallback`.

    A value past `MAX_KEEPALIVE_SEC` leaves too little margin to be worth
    trusting, and a non-positive one would send a frame for every frame of
    silence. Neither is accepted silently — a deployment that mistyped this
    would otherwise look configured while still dropping the session during a
    long pause.
    """
    try:
        secs = float(value)
    except (TypeError, ValueError):
        secs = 0.0
    if 0 < secs <= MAX_KEEPALIVE_SEC:
        return secs
    log.warning(f"Keep-alive interval {value!r} ignored — must be > 0 and <= "
                f"{MAX_KEEPALIVE_SEC:.0f}s; using {fallback:.0f}s")
    return fallback


KEEPALIVE_SEC = _resolve_keepalive_sec(
    os.environ.get("SARVAM_KEEPALIVE_SEC", 20.0), 20.0)


# Indic → English normally takes Saaras's one-call translate mode: the audio
# goes in and English comes out. It is the fastest path and it is what runs by
# default — but it NEVER returns what was actually said. Measured at the mandir
# on 2026-08-31: 24 finals recorded, zero characters of Gujarati in any of them.
#
# That matters beyond bookkeeping. Without the source text you cannot tell a
# mishearing from a mistranslation, cannot build a correction rule for a name
# that came out wrong, and cannot compare one translator against another —
# because you have nothing to feed the second one.
#
# Setting this forces the two-hop path that already exists for other language
# pairs: Saaras transcribes in Gujarati, then Mayura translates that text. Both
# halves are then recorded. It costs one extra API call per line and some
# latency, which is why it is not the default.
RECORD_SOURCE_TEXT = os.environ.get("SARVAM_RECORD_SOURCE", "").strip().lower() \
    in {"1", "on", "true", "yes"}


# ── Sentence-mode configuration ──────────────────────────────────────────
# Defaults chosen for a katha: the screen is read, not conversed with, so a
# whole sentence a little late beats half a sentence promptly. Every value is
# an env var so the mandir can be re-tuned on the day without a code change;
# CAPTION_SENTENCE_MODE=off restores the old per-utterance behaviour exactly.

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        log.warning(f"{name}: not a number, using {default}")
        return default


SENTENCE_MODE = (os.environ.get("CAPTION_SENTENCE_MODE", "on").strip().lower()
                 not in {"0", "off", "false", "no"})
# Hard ceiling on how stale a caption may be. A speaker in full flow yields
# neither punctuation nor a pause, and the hall still needs words.
SENTENCE_MAX_WAIT_SEC = _env_float("CAPTION_SENTENCE_MAX_WAIT_SEC", 8.0)
# The speaker has stopped; waiting longer buys nothing. 2.5s, not the 1.2s
# first guessed: on a real reading the median gap BETWEEN segments of one
# continuous sentence was 1.5s, so a 1.2s threshold released mid-sentence
# almost every time. Pauses between actual sentences ran 4.5s and up.
SENTENCE_QUIET_SEC    = _env_float("CAPTION_SENTENCE_QUIET_SEC", 2.5)
# Never let one caption become a wall of text on the screen.
#
# 180, not the 220 first guessed. 220 characters is roughly 35-40 words, which
# needs about 13 seconds to read and is given about 4 — captions arrive every
# ~4.0s on this speaker. The old ceiling permitted captions that were
# mathematically unreadable before being replaced, and no display timer can
# fix that; only a shorter caption can.
#
# 180 is about FOUR DISPLAYED LINES, not two.
#
# 🔴 The "about 90 characters fit one line" figure this was first justified by
# is wrong for the shipped layout. Measured in Chrome against the default
# 1280px area at 56px: 51 characters per line, so two lines hold about 102.
# The 90 came from the mandir PC's own layout and was applied to a different
# one without checking.
#
# The overlay now SPLITS a caption that does not fit into successive LINES
# rather than truncating it, so this is no longer a clipping ceiling. What it
# still bounds is how long one caption can occupy the screen: at 51 characters
# a line, 180 is about four lines, and with two on screen at a time that is
# two full screens — roughly 8 seconds at the ~1.9s line cadence. A caption
# much longer than that would still be scrolling through when the next several
# have been spoken.
#
# It also bounds what one drop costs. The queue drops a caption WHOLE rather
# than beheading it (see docs/how-it-works.md), so this ceiling is also the largest
# amount of speech a single drop can take from the hall.
#
# ⚠️ Raising this re-opens the fault. It is a READABILITY ceiling, not a
# storage one — the constraint is the sabha's eyes, not the wire.
#
# The default is a NAMED constant, not a literal in the call, so a test can
# assert the shipped value. Reading it back through _env_float() would only
# re-state the default the test itself passed in — a check reporting what was
# asked for rather than what happened, which is the exact disease that has
# already cost this project a mid-katha hour.
SENTENCE_MAX_CHARS_DEFAULT = 180
SENTENCE_MAX_CHARS    = int(_env_float("CAPTION_SENTENCE_MAX_CHARS", SENTENCE_MAX_CHARS_DEFAULT))
# A full stop under this many words is treated as a VAD artefact, not a
# sentence end. See the note in SentenceAssembler.add().
SENTENCE_MIN_WORDS    = int(_env_float("CAPTION_SENTENCE_MIN_WORDS", 5))
# How often the timer checks whether a held sentence has come due. Cheap, and
# it bounds the error on the two time-based releases above.
# How long a closing session waits for queued captions to be published.
SENTENCE_DRAIN_SEC    = _env_float("CAPTION_SENTENCE_DRAIN_SEC", 10.0)


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
        "needs_translation": bool,   # a separate translate step is required
      }
    """
    if source == target:
        return {"sarvam_mode": "transcribe", "sarvam_lang": source,
                "saaras_output_lang": source, "needs_translation": False}
    # The one-call shortcut asks Saaras to translate as it transcribes, so
    # the source text never exists. That is fine for Sarvam's own translator
    # and useless for any other: an external translator has nothing to work
    # from. So choosing one puts us on the two-hop path, exactly as asking
    # to record the source does.
    external = TRANSLATOR != "sarvam"
    if target == "en-IN" and source != "en-IN" \
            and not RECORD_SOURCE_TEXT and not external:
        return {"sarvam_mode": "translate", "sarvam_lang": source,
                "saaras_output_lang": "en-IN", "needs_translation": False}
    return {"sarvam_mode": "transcribe", "sarvam_lang": source,
            "saaras_output_lang": source, "needs_translation": True}


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


# ── Sentence assembly ────────────────────────────────────────────────────
#
# Saaras emits one "final" per VAD segment, not per sentence. A speaker who
# draws breath mid-sentence produces two finals that are each a fragment.
# Translating a fragment is where the English goes wrong, and Gujarati makes
# it worse than most: it is verb-final, so a fragment that stops before the
# verb has no predicate in it at all and the translator has to guess one.
#
# So we hold fragments until they look like a whole sentence, then translate
# once. That trades latency for sense — a deliberate trade for a katha, where
# the screen is read, not conversed with. Four things end the wait:
#
#   1. terminal punctuation      — the sentence closed itself
#   2. a quiet gap               — the speaker stopped; waiting buys nothing
#   3. the total-wait cap        — a speaker in full flow yields neither of
#                                  the above, and the hall still needs words
#   4. the character cap         — never let one caption become a wall
#
# 2 and 3 are what keep it live: staleness is bounded even if the speaker
# never pauses and Saaras never punctuates.

# A closing bracket or quote may follow the stop, so look past those. A bare
# digit before the stop (a decimal, "3.5") is not a sentence end.
_SENTENCE_END_RE = re.compile(r'[.!?।॥][)\]"\'\u201d\u2019]*\s*$')


class SentenceAssembler:
    """Buffers Saaras finals until they form a whole sentence.

    Every method returns either a sentence that is ready to translate, or
    None. It holds no clock of its own — the caller passes `now`, which is
    what lets the tests drive five minutes of katha in a millisecond.

    When `enabled` is False every fragment passes straight through, which is
    exactly the old per-utterance behaviour. That is the revert path: one
    env var, no code change, usable at 6am on the day.
    """

    # Defaults match the module constants, which were measured on a real
    # reading (see CAPTION_SENTENCE_* in docs/how-it-works.md). Constructing one
    # directly must not resurrect the values that measurement disproved.
    def __init__(self, *, max_wait_sec: float = 8.0, quiet_sec: float = 2.5,
                 max_chars: int = 220, min_words: int = 5,
                 enabled: bool = True) -> None:
        self.max_wait_sec = float(max_wait_sec)
        self.quiet_sec    = float(quiet_sec)
        self.max_chars    = int(max_chars)
        self.min_words    = int(min_words)
        self.enabled      = bool(enabled)
        self._parts: list[str] = []
        self._first_at: float  = 0.0
        self._last_at: float   = 0.0

    @property
    def pending(self) -> bool:
        return bool(self._parts)

    def _take(self) -> str:
        out = " ".join(self._parts)
        self._parts.clear()
        return out

    def add(self, text: str, now: float) -> str | None:
        """Feed one Saaras final in. Returns a sentence when one is ready."""
        frag = " ".join((text or "").split())
        if not frag:
            return None
        if not self.enabled:
            return frag
        if not self._parts:
            self._first_at = now
        self._parts.append(frag)
        self._last_at = now
        joined = " ".join(self._parts)
        # Punctuation alone is not evidence of a sentence. Saaras punctuates
        # a VAD segment: measured on a real Vachanamrut reading, 90 of 138
        # segments ended in a full stop and 49 of those were two words or
        # fewer. Releasing on the stop alone would leave the fragmentation
        # exactly as it was. Below the word floor the clock decides instead.
        if _SENTENCE_END_RE.search(joined) and len(joined.split()) >= self.min_words:
            return self._take()
        if len(joined) >= self.max_chars:
            return self._take()
        if now - self._first_at >= self.max_wait_sec:
            return self._take()
        return None

    def due(self, now: float) -> str | None:
        """Called on a timer. Releases a held sentence once the speaker has
        gone quiet, or once the total-wait cap is reached. Without this the
        last fragment before a pause would sit unshown until the speaker
        happened to say something else."""
        if not self.enabled or not self._parts:
            return None
        if now - self._first_at >= self.max_wait_sec:
            return self._take()
        if now - self._last_at >= self.quiet_sec:
            return self._take()
        return None

    def flush(self) -> str | None:
        """Release whatever is held, unconditionally — called when the
        session ends, so the last words of the katha are never swallowed."""
        if not self.enabled or not self._parts:
            return None
        return self._take()


def yt_lang_from_sarvam(code: str) -> str:
    """YouTube CC's `lang=` URL param accepts BCP-47; the primary subtag
    (en, gu, hi, …) is most broadly recognised on YouTube. Strip the
    region from "en-IN" → "en"."""
    return (code or "en").split("-")[0] or "en"


# 🔴 NEVER open the input device in exclusive mode.
#
# On Windows this tool shares one physical input with whatever else is using
# it — at the mandir, vMix holds the mixer feed on `Line In` through WASAPI in
# SHARED mode, and shared-mode capture alongside it is proven to work.
# `sd.InputStream(...)` with no `WasapiSettings` is shared, which is why it
# does. Do not add `extra_settings=sd.WasapiSettings(exclusive=True)`, and do
# not accept a "fix" that does.
#
# Demonstrated on the live machine 2026-08-31: an exclusive-mode open SUCCEEDS,
# and in succeeding it takes the device away from vMix. vMix's audio goes
# silent, its input keeps reporting `Running` with no error anywhere, restarting
# the input does not rebind it, and killing the offending process does not
# release it. **It takes a full vMix restart to recover** — which mid-katha
# means the hall and the stream lose all audio, not just captions.
#
# The failure is silent, survives the process that caused it, and vMix's own
# status lies about it. Exclusive mode is a footgun with no upside here.

# ── Device listing ────────────────────────────────────────────────────────────

def list_audio_devices() -> list[dict]:
    """Input devices, each labelled with the host API that reaches it.

    Windows exposes the same physical input several times, once per host API,
    under an IDENTICAL name. The mandir PC lists "Line In (Realtek(R) Audio)"
    four times — and at the 16 kHz this tool captures at, two of those four do
    not work: WASAPI refuses any rate but the endpoint's own 48 kHz mix format,
    and WDM-KS opens without error and then delivers no frames at all, which is
    worse. MME and DirectSound both work.

    So an operator picking by name alone has a one-in-four chance of choosing a
    dead entry and concluding the mixer feed is broken — five minutes before a
    katha, with a full hall. Appending the host API is what makes the four
    distinguishable, and `works_at_16k` says outright which ones to avoid
    rather than leaving it to be discovered live.
    """
    import sounddevice as sd
    hostapis = sd.query_hostapis()
    devices = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        api = ""
        try:
            api = hostapis[d["hostapi"]]["name"]
        except Exception:
            pass
        # Ask the driver rather than guessing from the API name — this is the
        # same check that fails at capture time, asked early enough to warn.
        works = True
        try:
            sd.check_input_settings(device=i, samplerate=16000,
                                    channels=1, dtype="int16")
        except Exception:
            works = False
        devices.append({
            "id": str(i),
            "name": f"{d['name']} — {api}" if api else d["name"],
            "channels": d["max_input_channels"],
            "hostApi": api,
            "worksAt16k": works,
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


async def audio_from_device(device_id: str | None, chunk_ms: int = 500,
                            state: dict | None = None):
    """Capture from an input device.

    `state` is the session state dict, and is written to purely so
    `/api/overlay-status` can report the drop count from outside. The
    warning below is the only other place this number surfaces, and a log
    line on an unattended mandir PC is not something anyone reads mid-katha.
    """
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
            if state is not None:
                state["capture_queue_drops"] = drops["count"]
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


def apply_rules_safely(text: str, rules: list[Rule]) -> tuple[str, list[str]]:
    """`apply_rules`, except it may never hand back nothing.

    The rule pass runs AFTER `translate_line` has guaranteed a non-empty
    caption, and it can undo that guarantee: an empty `replacement` is accepted
    by the API and by `rules.json`, so a rule like `(".*" -> "")` erases the
    whole line. Downstream that broadcasts `""` and the caption bar in a full
    hall goes blank, with nothing logged on any path.

    Deleting a WORD with an empty replacement is legitimate and still works.
    Only the case that empties the ENTIRE caption is refused, and it degrades
    to the un-corrected text rather than to nothing. Deliberately suppressing
    an utterance is spelled "…" — the exclusion marker — which is not blank and
    passes through untouched.
    """
    corrected, fired = apply_rules(text, rules)
    if text.strip() and not corrected.strip():
        log.warning(
            f"rules {fired} emptied a caption — keeping the un-corrected text. "
            f"A blank caption bar in a full hall is the one outcome with no "
            f"recovery; check the replacement on those rules."
        )
        return text, []
    return corrected, fired


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
                    audio_level: float | None = None,
                    source_text: str | None = None,
                    source_text_lang: str | None = None) -> None:
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
            # What the speech service actually returned, and which language it
            # is in. On the default one-call path this is the English and
            # `source_text_lang` says so — the Gujarati genuinely does not
            # exist. With SARVAM_RECORD_SOURCE on it is the Gujarati, and this
            # is the only place it is ever written down.
            "source_text":      source_text,
            "source_text_lang": source_text_lang,
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

# The vendor SDK opens its WebSocket with no ping settings, so it silently
# inherits the websockets library defaults: ping_interval=20, ping_timeout=20.
# That means OUR client pings every 20s and hangs up if Sarvam has not ponged
# within 20s.
#
# Measured 2026-08-31 against the live service: five idle sessions were opened,
# spoken to briefly, then sent nothing. Four survived three minutes. One died
# at 50s with `1011 internal error — "keepalive ping timeout"` — our side
# closing the connection, not Sarvam's. That is the only failure actually
# observed, and it is self-inflicted.
#
# The SDK exposes no way to configure this (`client.py` calls the connect
# helper with only a URL and headers), so the helper is wrapped here at import
# time. The timeout is raised rather than disabled: `ping_timeout=None` would
# stop us ever noticing a genuinely dead connection, which trades a rare false
# teardown for a permanent silent one. A dead TCP connection is still caught,
# just after ~80s instead of ~40s.
WS_PING_INTERVAL_SEC = float(os.environ.get("SARVAM_WS_PING_INTERVAL_SEC", "20"))
WS_PING_TIMEOUT_SEC  = float(os.environ.get("SARVAM_WS_PING_TIMEOUT_SEC",  "60"))


def _widen_sarvam_ws_ping_timeout() -> bool:
    """Give Sarvam longer to answer a protocol ping before we hang up on it.

    Returns whether the patch was applied, so a vendor SDK that changes shape
    degrades to today's behaviour with a warning rather than an import crash.
    """
    try:
        import functools
        import sarvamai.speech_to_text_streaming.client as _stc
        original = getattr(_stc, "websockets_client_connect", None)
        if original is None or getattr(original, "_ping_widened", False):
            return False
        patched = functools.partial(original,
                                    ping_interval=WS_PING_INTERVAL_SEC,
                                    ping_timeout=WS_PING_TIMEOUT_SEC)
        patched._ping_widened = True
        _stc.websockets_client_connect = patched
        return True
    except Exception as e:
        log.warning(f"Could not widen the Sarvam WebSocket ping timeout ({e!r}); "
                    f"falling back to the library default of 20s. A rare "
                    f"'keepalive ping timeout' disconnect becomes more likely.")
        return False


# Floor on the interval between two connection attempts, measured attempt-start
# to attempt-start rather than from the end of the previous session. A session
# that dies the instant it opens therefore still costs a full interval before
# the next one — otherwise the clean-close path (which resets the backoff by
# design, so a flip reconnects promptly) would spin against Sarvam's rate
# limiter for as long as the fault lasted.
# Clamped, not merely defaulted: #17 asked for a floor "regardless of how the
# session ended", and a floor an operator can set to 0 is not a floor. The
# environment can widen it, never remove it.
RECONNECT_MIN_INTERVAL_SEC = max(
    0.5, float(os.environ.get("RECONNECT_MIN_INTERVAL_SEC", "1.0")))
RECONNECT_MAX_INTERVAL_SEC = 8.0

# A session lasting at least this long counts as having worked, which resets
# the reconnect backoff. Comfortably longer than the ~1s a connect-then-die
# fault takes, and far shorter than any real stretch of katha.
HEALTHY_SESSION_SEC = float(os.environ.get("HEALTHY_SESSION_SEC", "30"))

# What a browser tab is told when nothing is capturing. `disconnected` is
# reserved for a session that wanted a connection and hasn't got one — a
# stopped tool is not a fault and must not paint like one.
CONNECTION_IDLE: dict = {"type": "connection", "state": "idle", "attempt": 0,
                         "reason": None, "retry_in_sec": None}


def _next_backoff(current: float) -> float:
    """Spacing after a failed attempt: never under the floor, never over the cap."""
    return min(max(current * 1.7, RECONNECT_MIN_INTERVAL_SEC), RECONNECT_MAX_INTERVAL_SEC)


async def _announce_connection(broadcaster: "Broadcaster", state: dict | None, link_state: str,
                               *, attempt: int = 0, reason: str | None = None,
                               retry_in_sec: float | None = None) -> None:
    """Publish the state of the link to the speech service.

    Kept on `state` as well as broadcast, because a broadcast only reaches the
    tabs that are open at the time. `handle_ws` hands the stored value to each
    tab as it connects, so one opened (or refreshed) mid-outage paints the
    fault rather than an innocent-looking blank screen.
    """
    msg = {"type": "connection", "state": link_state, "attempt": attempt,
           "reason": reason, "retry_in_sec": retry_in_sec}
    if state is not None:
        state["connection"] = dict(msg)
    try:
        await broadcaster.send(msg)
    except Exception:
        pass


async def _wait_before_retry(seconds: float, stop_event: asyncio.Event) -> bool:
    """Hold for `seconds`, or return the moment the operator presses Stop.

    True means Stop fired. This is the one wait in the supervisor that runs on
    the event loop's clock instead of `time.time()`, which is why `sarvam_loop`
    accepts it as a parameter: a test asserting how far apart attempts are
    spaced substitutes a version costing no wall clock. Production passes none.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def sarvam_loop(audio_gen, broadcaster: Broadcaster, use_pp: bool, stop_event: asyncio.Event,
                      state: dict, sarvam_cfg: dict | None = None, gate_cfg: dict | None = None,
                      client=None, retry_wait=None):
    """Sarvam streaming session supervisor.

    Reads the current (source, target) lang pair from
    `state["source"]` / `state["target"]` on each reconnect iteration so a
    mid-session ⇆ flip or any other direction change picks up automatically.
    Stashes the live ws on `state["sarvam_ws"]` so the flip handler can
    close it to force a reconnect.

    `client` is the speech-service client. Left at None — as production always
    does — it is an `AsyncSarvamAI` built from SARVAM_API_KEY. Passing one in
    is the seam the tests use: with a fake client the whole supervisor runs
    with no network, no API key and no audio device. See tests/fakes.py.

    `retry_wait` is the between-attempts hold, `_wait_before_retry` by default.
    It is a parameter for the same reason: it is the only wait here that a
    virtual clock cannot reach.
    """
    retry_wait = retry_wait or _wait_before_retry
    api_key = os.environ.get("SARVAM_API_KEY", "")
    if client is None and not api_key:
        log.error("SARVAM_API_KEY not set")
        return

    # Everything /api/overlay-status reports about the input side is reset
    # here rather than at the end of the previous run. A carried-over
    # timestamp is the one failure mode this board exists to prevent: it
    # would make a session that has never seen a frame read as freshly fed.
    if state is not None:
        state.update({
            "capture_running":     True,
            "capture_started_at":  time.time(),
            "last_audio_at":       None,
            "last_loud_audio_at":  None,
            "last_final_at":       None,
            "capture_queue":       None,
            "capture_queue_max":   None,
            "capture_queue_drops": 0,
            "session_queue_drops": 0,
            "input_peak":          None,
        })

    # Base kwargs from the UI start payload — model + VAD knobs survive
    # across direction changes. Mode + language_code get derived per-
    # iteration from state["source"]/state["target"] so a flip / dropdown
    # change takes effect on reconnect.
    cfg = sarvam_cfg or {}
    # Validate against SARVAM_MODEL_IDS rather than trusting the posted
    # string outright — a browser tab with stale localStorage from before
    # this fix could still be holding a withdrawn id (e.g. "saaras:v2").
    requested_model = cfg.get("model")
    model = requested_model if requested_model in SARVAM_MODEL_IDS else DEFAULT_SARVAM_MODEL
    base_kwargs = dict(
        model                = model,
        sample_rate          = cfg.get("sample_rate",          16000),
        input_audio_codec    = cfg.get("input_audio_codec",    "pcm_s16le"),
        high_vad_sensitivity = bool(cfg.get("high_vad_sensitivity", True)),
        vad_signals          = bool(cfg.get("vad_signals",     True)),
    )

    # Client-side gate (not sent to Sarvam — pre-filters silence locally).
    gate = gate_cfg or {}
    gate_peak_threshold = float(gate.get("silence_threshold", 0.010))
    gate_hangover_sec   = float(gate.get("hangover_sec",      1.5))
    # Interval of 0 disables the keep-alive; the switch and the interval are
    # separate settings so an operator can turn it off without losing a tuned
    # interval, and either can come from .env or the start payload.
    if gate.get("keepalive", KEEPALIVE_ENABLED):
        keepalive_sec = _resolve_keepalive_sec(
            gate.get("keepalive_sec", KEEPALIVE_SEC), KEEPALIVE_SEC)
    else:
        keepalive_sec = 0.0
    log.info(f"Client silence gate: peak ≥ {gate_peak_threshold*100:.2f}% of full-scale, "
             f"hangover {gate_hangover_sec:.2f}s, "
             + (f"keep-alive every {keepalive_sec:.0f}s" if keepalive_sec else "keep-alive off"))

    if client is None:
        from sarvamai import AsyncSarvamAI
        _widen_sarvam_ws_ping_timeout()
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
    # The pump is the last point where "audio is still arriving from the
    # source" is a fact rather than an inference, so it is where
    # /api/overlay-status reads it from. A stalled sender leaves the pump
    # running, and that difference is exactly what the board has to show.
    if state is not None:
        state["capture_queue"]     = audio_q
        state["capture_queue_max"] = audio_q.maxsize
    async def _audio_pump():
        import numpy as np
        pump_drops = 0
        try:
            async for pcm, ts in audio_gen:
                if stop_event.is_set():
                    break
                if state is not None:
                    now = time.time()
                    state["last_audio_at"] = now
                    # 🔴 Loudness is measured HERE and not in the sender, even
                    # though the sender already computes the same peak for the
                    # gate. The sender only runs inside a live speech-service
                    # session; with the link down it never runs at all, so the
                    # board reported "nothing above the silence gate" for a
                    # microphone that was delivering perfectly good speech —
                    # sending the reader to the mixer for a fault that was in
                    # the network. Found by driving the real endpoint with a
                    # bad API key. Whether there is SOUND in the audio is a
                    # property of the input, and must not depend on whether
                    # anything downstream is reachable.
                    try:
                        peak = float(np.abs(np.frombuffer(pcm, dtype=np.int16)).max()) / 32767.0
                        if peak >= gate_peak_threshold:
                            state["last_loud_audio_at"] = now
                        # Its own key, not `last_audio_level`: that one is the
                        # sender's 250ms window, read per FINAL by the
                        # SessionRecorder, and the board must not redefine a
                        # recorded column. Same reason as the timestamp above
                        # — the sender does not run with the link down, and
                        # "how loud is the microphone" is the first question
                        # asked when captions stop. It must still have an
                        # answer when the link is what broke.
                        state["input_peak"] = round(peak, 4)
                    except Exception:
                        pass
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
                    pump_drops += 1
                    if state is not None:
                        state["session_queue_drops"] = pump_drops
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

    def _audio_source_finished() -> bool:
        # No point reconnecting to Sarvam if no audio will ever arrive.
        return audio_pump_done.is_set() and audio_q.empty()

    # Sarvam's WS sometimes drops with `no close frame received or sent`
    # (server-side idle timeout, transient network blip). Retry the connect
    # with exponential backoff so a brief drop doesn't end the session —
    # the operator only sees an interruption if Sarvam stays unreachable
    # past the backoff cap (8 s). The audio_gen, captionText state on each
    # browser tab, and PP message id all survive across reconnects.
    import websockets.exceptions as _wse
    backoff_sec = 0.0
    attempt = 0
    last_attempt_at: float | None = None
    try:
        while not stop_event.is_set():
            if _audio_source_finished():
                log.warning("audio pump exited and queue is empty — ending sarvam_loop")
                break

            # Hold off until this attempt is due. Spacing is measured from the
            # PREVIOUS attempt, so however the last session ended — clean
            # close, drop, unexpected error — the interval is the same and no
            # path can produce a tight retry loop.
            if last_attempt_at is not None:
                due_in = last_attempt_at + max(RECONNECT_MIN_INTERVAL_SEC, backoff_sec) - time.time()
                if due_in > 0:
                    log.info(f"Next Sarvam connection attempt in {due_in:.1f}s")
                    if await retry_wait(due_in, stop_event):
                        break
                if stop_event.is_set():
                    break

            last_attempt_at = time.time()
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
            reason = "closed"
            try:
                await _sarvam_session(
                    client, connect_kwargs, _queue_consumer(), broadcaster,
                    pp_session, pp_id, stop_event,
                    gate_peak_threshold, gate_hangover_sec, attempt,
                    source=source, target=target, pipeline=pipe,
                    mt_session=mt_session,
                    api_key=api_key, state=state,
                    keepalive_sec=keepalive_sec,
                )
                if stop_event.is_set():
                    break
                # Returned without exception (WS closed cleanly) but the
                # operator didn't stop — could be a flip that closed the ws on
                # purpose, or the service hanging up on us.
                #
                # This escalates like any other ending. A clean close is not
                # self-evidently benign: a service that accepts a connection
                # and immediately closes it cleanly would, if this reset to
                # the floor, be retried once a second for as long as the fault
                # lasted — ~3600 attempts an hour into a rate limiter, which is
                # the exact failure the floor exists to prevent. What earns a
                # reset is a session that actually WORKED, handled below.
                log.info("Sarvam WS closed cleanly — reconnecting")
                backoff_sec = _next_backoff(backoff_sec)
            except asyncio.CancelledError:
                raise
            except _wse.ConnectionClosed as e:
                log.warning(f"Sarvam WS dropped: {type(e).__name__}: {e}")
                reason = "dropped"
                backoff_sec = _next_backoff(backoff_sec)
            except Exception as e:
                log.error(f"Sarvam session error: {e!r}", exc_info=True)
                reason = "error"
                backoff_sec = _next_backoff(backoff_sec)

            # A session that ran for a while did its job, so whatever ended it
            # is a new fault rather than the last one repeating — start the
            # backoff over. This is what keeps a deliberate direction flip
            # cheap: a flip after an hour of katha reconnects at the floor,
            # while a service that keeps dying on contact keeps backing off.
            # Health is measured by how long it lasted, not by how it ended.
            if time.time() - last_attempt_at >= HEALTHY_SESSION_SEC:
                backoff_sec = 0.0

            if stop_event.is_set() or _audio_source_finished():
                break

            # The link is down, so whatever is on the hall's screen no longer
            # matches anything being said. Blank every surface and name the
            # fault for the operator — a stale caption is worse than none, and
            # a blank bar they cannot explain is worse than a labelled one.
            due_at = last_attempt_at + max(RECONNECT_MIN_INTERVAL_SEC, backoff_sec)
            await _announce_connection(
                broadcaster, state, "disconnected", attempt=attempt, reason=reason,
                retry_in_sec=round(max(0.0, due_at - time.time()), 1),
            )
            await broadcaster.send({"type": "clear"})
    finally:
        # First thing in the finally, deliberately: anything below can raise,
        # and a status board still claiming to be capturing after the loop has
        # left is the exact lie it was built to stop telling. The queue object
        # goes with it — its depth means nothing once nothing is feeding it.
        if state is not None:
            state["capture_running"] = False
            state["capture_ended_at"] = time.time()
            state["capture_queue"] = None
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
        # Back to idle, not disconnected: nothing is trying to connect any
        # more, so a tab opening after this must not be shown a fault.
        await _announce_connection(broadcaster, state, "idle")
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


# ── A second translator ──────────────────────────────────────────────────
#
# Mayura is competent at Gujarati and it is fast, but it cannot be *told*
# anything: there is no prompt, so there is no way to say that this is a
# Hindu devotional discourse, that કથા here is the recital of scripture and
# not a "story", or how the swami's name is spelled. On the first live
# sample it rendered "ભગવાનની કથાનો આરંભ કરીએ છીએ" as "the Lord" — the
# central noun simply vanished.
#
# A general model can be told all of that, and told it on every line. That
# is the entire reason for a second backend; speed is a constraint on it,
# not the point of it. So: thinking off, temperature zero, a hard timeout,
# and Mayura still sitting underneath as the fallback. A caption that is
# late is worse than a caption that is merely imperfect, and a blank
# caption bar is worse than both.

GEMINI_URL_TMPL = ("https://generativelanguage.googleapis.com/v1beta/"
                   "models/{model}:generateContent")

# Owner's decision, 2026-09-01, on measured evidence: briefed Gemini scored
# 24/24 on the passage's terminology against Mayura's 13/24, and on six
# minutes of the speaker's own katha it produced readable English where
# Mayura produced word-salad ("Maharaj wrote the Shikshapatri" vs
# "Maharaja Shikshapatri is written"). Sarvam stays underneath as the
# fallback and remains one env var away.
# The set of translators that actually exist. An unrecognised value used to be
# the worst kind of wrong: `derive_pipeline` read "not sarvam" and forced the
# slow two-hop path, `translate_line` read "not gemini" and went straight to
# Mayura, and the startup log read "not sarvam, key present" and announced
# Gemini. One letter -- CAPTION_TRANSLATOR=gemeni -- bought the expensive
# pipeline, delivered the cheap translator, and printed the line the mandir PC
# uses to verify the switchover. Three readers, three different answers, no
# complaint anywhere.
TRANSLATORS = ("gemini", "sarvam")

_requested_translator = os.environ.get("CAPTION_TRANSLATOR", "gemini").strip().lower()
TRANSLATOR = _requested_translator if _requested_translator in TRANSLATORS else "gemini"

# Deliberately NOT a hard exit. This is read at import, so a refusal would mean
# a server that does not start -- and the mandir launches it by double-clicking
# a .bat, where "did not start" is invisible and indistinguishable from "did
# not work". A running server on the documented default still captions the
# katha; a blank overlay is the one outcome with no recovery. So: correct
# defaults, and say so loudly enough that the log cannot be misread.
if _requested_translator not in TRANSLATORS:
    log.error(
        f"CAPTION_TRANSLATOR={_requested_translator!r} is not one of "
        f"{list(TRANSLATORS)} -- almost certainly a typo in .env. "
        f"Falling back to {TRANSLATOR!r}. Fix .env and restart to be sure "
        f"you are getting what you asked for."
    )
# Read from the environment (and therefore from .env). It is never
# written to a file in this repo — both repos are public.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Measured live 2026-08-31: "gemini-2.5-flash" returns 404, "no longer
# available to new users". gemini-3.1-flash-lite answers in ~530ms and
# accepts the thinking field; the newest Flash models were repeatedly
# "experiencing high demand" and timed out, which is not what you want
# in front of a hall. Revisit once #38 has scored them.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
# 2.5s was a guess and it was too tight: measured median is ~1.8s, so a
# meaningful share of healthy calls would have timed out and been
# answered by Mayura instead — the better translator silently absent.
# The total end-to-end budget is #35's to set; this only stops the
# fallback firing on calls that were going to succeed.
GEMINI_TIMEOUT_SEC = _env_float("GEMINI_TIMEOUT_SEC", 5.0)
# How many previously translated lines to offer as context. Enough to keep
# pronouns and topic consistent; small enough not to cost latency.
GEMINI_CONTEXT_LINES = int(_env_float("GEMINI_CONTEXT_LINES", 3))

# The domain brief and the glossary are the two things Mayura cannot take.
# Both live in a file the mandir can edit without touching code, because the
# people who know that vocabulary are not the people who deploy this.
GLOSSARY_PATH = os.environ.get("CAPTION_GLOSSARY", "glossary.json")


def load_glossary(path: str = "") -> tuple[str, dict]:
    """Read the domain brief and term glossary. Absent file → no opinions.

    Shape:
        {"brief":     "...",
         "reference": "the day's passage in published English, optional",
         "terms":     {"<source term>": "<English>", ...}}

    `reference` is folded into the brief. It exists because of what a real
    Vachanamrut reading showed: the passage's central term — શાપિત બુદ્ધિ,
    "cursed intellect" — was misheard as શાંતિ and શાર્પ and came out as
    "He's crazy", "He's brainwashed", "He's real smooth" and "He becomes a
    victim", five renderings of one idea, none of them it. A glossary entry
    cannot repair a mishearing. Telling the model what passage is being read
    can, because it makes the right words the expected ones.
    """
    path = path or GLOSSARY_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return "", {}
    except Exception as e:
        log.warning(f"glossary {path}: {type(e).__name__}: {e} — ignoring")
        return "", {}
    brief = str(data.get("brief") or "").strip()
    reference = str(data.get("reference") or "").strip()
    if reference:
        brief = (brief + "\n\nThe speaker is working through this passage. "
                 "Its published English translation is below — match its "
                 "terminology and names. Do NOT copy from it: translate what "
                 "was actually said, which will often be the speaker's own "
                 "words about the passage rather than the passage itself."
                 f"\n{reference}").strip()
    terms = data.get("terms") or {}
    if not isinstance(terms, dict):
        log.warning(f"glossary {path}: 'terms' is not an object — ignoring it")
        terms = {}
    return brief, {str(k): str(v) for k, v in terms.items()}


# The passage this tool was built for is *about* being cursed for hurting a
# sant or failing one's parents. Left to default thresholds that reads as
# harassment, and a block is indistinguishable from a timeout: both return
# nothing and silently demote to the weaker translator. Scripture is not
# abuse, and the hall should not lose a line because a filter cannot tell.
_GEMINI_SAFETY = [
    {"category": c, "threshold": "BLOCK_NONE"} for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

# A caption is one sentence. This only has to stop a runaway answer holding
# the caption bar; it is not a quality knob.
GEMINI_MAX_OUTPUT_TOKENS = int(_env_float("GEMINI_MAX_OUTPUT_TOKENS", 256))

# Models that answered 400 on `thinkingConfig`. gemini-3.1-flash-lite accepts
# it and gemini-3.5-flash-lite rejects it, so it cannot be sent blind and the
# vendor documents no way to ask in advance. We try once, remember, move on.
_NO_THINKING_CONFIG: set[str] = set()


def describe_gemini_refusal(data: dict) -> str:
    """Why an answer was unusable, in words a log reader can act on.

    A safety block, a truncation and an empty response all look identical to
    the caller — nothing comes back and Mayura answers instead. Naming them
    apart is what makes a pattern visible over four hours.
    """
    try:
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            return f"blocked: {blocked}"
        candidates = data.get("candidates") or []
        if not candidates:
            return "no candidates"
        finish = candidates[0].get("finishReason")
        if finish == "SAFETY":
            return "blocked: SAFETY"
        if finish == "MAX_TOKENS":
            return "truncated"
        return f"empty ({finish})" if finish else "empty"
    except (AttributeError, TypeError, IndexError):
        return "unreadable"


def build_gemini_request(text: str, *, source_lang: str, target_lang: str,
                         brief: str = "", glossary: dict | None = None,
                         context: list[str] | None = None,
                         allow_thinking_config: bool = True) -> dict:
    """The request body, built without touching the network so that what we
    ask for can be asserted in a test.

    The instruction is deliberately blunt about output shape: a caption bar
    has no room for a model's preamble, and "Here is the translation:" on a
    temple screen is worse than a clumsy sentence.
    """
    # The operator label carries a native-script suffix
    # ("Gujarati  ગુજરાતી"); the model wants the English name only.
    src = langname(source_lang).split("  ")[0].strip()
    tgt = langname(target_lang).split("  ")[0].strip()

    rules = [
        f"Translate the {src} line below into {tgt}.",
        "Output ONLY the translation. No preamble, no notes, no quotes, "
        "no transliteration, no alternatives.",
        "Keep proper nouns, deity names and place names as names.",
        "Render it as a caption: natural, dignified, and short enough to "
        "read on a screen while the speaker carries on.",
        "If the line is incomplete, translate what is there and do not "
        "invent an ending.",
    ]
    if brief:
        rules.insert(0, f"Context: {brief}")
    if glossary:
        pairs = "; ".join(f"{k} = {v}" for k, v in glossary.items())
        rules.append(f"Use these renderings for these terms: {pairs}.")

    parts: list[str] = ["\n".join(rules)]
    # The limit is checked HERE as well as at the source of the list, because
    # `context[-0:]` is the whole list: a `GEMINI_CONTEXT_LINES` of 0 would
    # have offered every line of the session so far, which is the opposite of
    # what setting it to 0 asks for. Off must mean off.
    if context and GEMINI_CONTEXT_LINES > 0:
        prior = "\n".join(context[-GEMINI_CONTEXT_LINES:])
        parts.append(
            "The previous lines, already translated, for continuity of "
            f"pronouns and topic only — do NOT translate or repeat them:\n{prior}"
        )
    parts.append(f"The line to translate:\n{text}")

    gen: dict = {
        "temperature": 0,
        "candidateCount": 1,
        "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
    }
    if allow_thinking_config:
        # A reasoning pass costs seconds, and a caption that arrives after
        # the speaker has moved on is not a caption.
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    return {
        "contents": [{"role": "user", "parts": [{"text": "\n\n".join(parts)}]}],
        "generationConfig": gen,
        "safetySettings": _GEMINI_SAFETY,
    }


def parse_gemini_response(data: dict) -> str | None:
    """Pull the translation out, or None meaning 'fall back'.

    Every shape that is not a usable translation must return None — an empty
    candidate list, a safety block, a truncated response. None is what makes
    the caller try Mayura instead; anything else risks putting a stray token
    on the hall screen.
    """
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        out = "".join(p.get("text") or "" for p in parts).strip()
    except (AttributeError, TypeError, IndexError):
        return None
    # Models like to wrap a translation in quotes; a caption bar does not
    # want them.
    if len(out) >= 2 and out[0] in "\"'\u201c" and out[-1] in "\"'\u201d":
        out = out[1:-1].strip()
    return out or None


async def _gemini_translate(session: aiohttp.ClientSession, api_key: str,
                            text: str, source_lang: str, target_lang: str,
                            *, brief: str = "", glossary: dict | None = None,
                            context: list[str] | None = None,
                            model: str = "",
                            timeout_sec: float = 0.0) -> str | None:
    """One Gemini call. Returns None on ANY failure, so the caller falls back.

    Note the contract differs from `_mayura_translate`, which returns the
    source text on failure. Here None is meaningful: it means "I have no
    answer, use the other backend" — and only after that does returning the
    source text become the right thing to do.
    """
    if not api_key or not text:
        return None
    model = model or GEMINI_MODEL
    timeout_sec = timeout_sec or GEMINI_TIMEOUT_SEC

    async def attempt(allow_thinking: bool):
        """Returns (text, retry_without_thinking)."""
        body = build_gemini_request(
            text, source_lang=source_lang, target_lang=target_lang,
            brief=brief, glossary=glossary, context=context,
            allow_thinking_config=allow_thinking)
        async with session.post(
            GEMINI_URL_TMPL.format(model=model),
            json=body,
            headers={"x-goog-api-key": api_key,
                     "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            if resp.status != 200:
                snippet = (await resp.text())[:200]
                # Some models 400 on the thinking field that others require.
                # There is no documented way to ask in advance, so we find
                # out once per model and remember.
                if resp.status == 400 and allow_thinking:
                    log.info(f"Gemini {model}: 400 with thinkingConfig — "
                             "retrying without, and remembering")
                    return None, True
                log.warning(f"Gemini {model}: HTTP {resp.status}: {snippet!r}")
                return None, False
            data = await resp.json()
            out = parse_gemini_response(data)
            if out is None:
                # Name it: a safety block, a truncation and an empty answer
                # are indistinguishable to the caller, and only the pattern
                # over hours tells you which problem you have.
                log.warning(f"Gemini {model}: {describe_gemini_refusal(data)} "
                            "— falling back")
            return out, False

    try:
        allow = model not in _NO_THINKING_CONFIG
        out, retry = await attempt(allow)
        if retry:
            _NO_THINKING_CONFIG.add(model)
            out, _ = await attempt(False)
        return out
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        log.warning(f"Gemini: no answer within {timeout_sec:.1f}s — falling back")
        return None
    except Exception as e:
        log.warning(f"Gemini: {type(e).__name__}: {e} — falling back")
        return None


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


def effective_translator(gemini_key: str = "") -> str:
    """Which rung `translate_line` will actually try first, given the key it has.

    Exists so that no log line has to re-derive it. The configured value and the
    reachable one are not the same thing: CAPTION_TRANSLATOR=gemini with an empty
    GEMINI_API_KEY means Gemini is never attempted, and because it is never
    attempted the per-line "falling back to Mayura" never prints either. A label
    reading "gemini" over Mayura's captions, with zero fallbacks logged, is the
    exact shape of the bug this whole change set exists to remove.
    """
    if TRANSLATOR == "gemini" and (gemini_key or GEMINI_API_KEY):
        return "gemini"
    return "sarvam"


async def translate_line(session: aiohttp.ClientSession, text: str, *,
                         source_lang: str, target_lang: str,
                         sarvam_key: str = "", gemini_key: str = "",
                         brief: str = "", glossary: dict | None = None,
                         context: list[str] | None = None,
                         mayura_model: str | None = None) -> str:
    """Translate one line. Best backend first; never returns nothing.

    The order is the whole point. Gemini can be told what this event is and
    what its vocabulary means, so it goes first when configured. Mayura sits
    underneath because it is fast and always there, and Mayura's own last
    resort is to hand back the source text. Three rungs, and the bottom one
    still puts words on the screen — a blank caption bar in a full hall is
    the one outcome with no recovery.
    """
    if TRANSLATOR == "gemini" and gemini_key:
        out = await _gemini_translate(
            session, gemini_key, text,
            source_lang=source_lang, target_lang=target_lang,
            brief=brief, glossary=glossary, context=context,
        )
        # Same reason as the door below: whitespace is truthy and blank.
        # A model that answers with a space has not answered.
        if (out or "").strip():
            return out.strip()
        log.info("Gemini gave no answer — falling back to Mayura for this line")
    out = await _mayura_translate(
        session, sarvam_key, text,
        source_lang=source_lang, target_lang=target_lang, model=mayura_model,
    )
    # The promise in the docstring is kept HERE, not delegated. It used to rest
    # entirely on _mayura_translate remembering to `return text` in each of its
    # three failure paths; changing any one of them to return the empty string
    # left all 101 tests passing and put a blank bar in front of the hall.
    # A total function should be total at its own door.
    #
    # `.strip()` matters and is not tidying: a whitespace-only answer is
    # truthy in Python and blank on a screen. Truthiness is the wrong test
    # for "did the hall get words".
    return (out or "").strip() or text


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
                          state: dict | None = None,
                          keepalive_sec: float = 0.0) -> None:
    """One Sarvam WebSocket session. Returns when WS closes cleanly or when
    stop_event is set. Raises websockets.exceptions.ConnectionClosed (or
    other) when the WS dies unexpectedly — the outer reconnect loop in
    sarvam_loop catches that and retries.

    Pipeline:
        if pipeline.needs_translation:
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
    needs_mt    = pipe["needs_translation"]
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
        await _announce_connection(broadcaster, state, "connected", attempt=attempt)

        # The brief and glossary are read once per session, so the mandir
        # can edit glossary.json between sessions and have it take effect on
        # the next start without a restart of anything else.
        brief, glossary = load_glossary()
        # Always say which translator is REALLY in the path, not which one was
        # asked for. The two came apart once already — the switch was set and
        # the pipeline never called the translator at all — and the captions
        # looked plausible throughout, so nothing on screen gave it away.
        if TRANSLATOR == "sarvam":
            log.info("Translator: Sarvam"
                     + (" (Mayura, two-hop)" if needs_mt else " (Saaras one-call)"))
        elif not GEMINI_API_KEY:
            log.warning(f"Translator: {TRANSLATOR} was requested but there is no "
                        "GEMINI_API_KEY — every line will come from Mayura")
        elif not needs_mt:
            log.error(f"Translator: {TRANSLATOR} was requested but this direction "
                      "needs no separate translate step, so it will NOT be used")
        else:
            log.info(f"Translator: Gemini {GEMINI_MODEL}, Mayura underneath"
                     + (f", {len(glossary)} glossary terms" if glossary else ", NO glossary")
                     + (", passage reference loaded" if "published English" in brief else ""))
        # The last few English lines, offered to the model for continuity of
        # pronouns and topic. Display target only.
        recent_lines: list[str] = []

        # ── Sentence assembly ────────────────────────────────────────
        # Saaras hands us one final per VAD segment, which is not the same
        # thing as a sentence. `assembler` holds the fragments; whatever it
        # releases is what actually gets translated and shown.
        assembler = SentenceAssembler(
            max_wait_sec = SENTENCE_MAX_WAIT_SEC,
            quiet_sec    = SENTENCE_QUIET_SEC,
            max_chars    = SENTENCE_MAX_CHARS,
            min_words    = SENTENCE_MIN_WORDS,
            enabled      = SENTENCE_MODE,
        )

        async def _emit_sentence(text: str) -> None:
            """Translate one assembled sentence and push it to every surface.

            Called from two places — the message loop, when a sentence
            closes itself, and the audio sender, when the speaker stops
            talking mid-sentence. Both paths must produce identical output,
            which is why this is one function and not two code paths.
            """
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

            # One translate call per wanted language, in parallel. Not
            # bounded: `wanted` is the set of distinct target languages, so
            # it is as wide as the number of enabled feeds — single digits.
            translated: dict[str, str] = {saaras_out: text}
            if wanted and not mt_disabled:
                t_mt = time.time()
                async def _do_one(tgt: str) -> tuple[str, str]:
                    out = await translate_line(
                        mt_session, text,
                        source_lang=saaras_out, target_lang=tgt,
                        sarvam_key=api_key, gemini_key=GEMINI_API_KEY,
                        brief=brief, glossary=glossary,
                        # Only the display target gets continuity context;
                        # a feed in another language has its own thread of
                        # meaning and would be confused by English lines.
                        context=recent_lines if tgt == target else None,
                        mayura_model=mayura_model_for(saaras_out, tgt),
                    )
                    return tgt, out
                # return_exceptions=True is load-bearing. With it False, one
                # raise anywhere in a fan-out task propagated to _publish, whose
                # only done-callback is in_flight.discard -- so nothing was ever
                # broadcast and the hall kept the PREVIOUS caption. A stale line
                # is the worst outcome this project has: it is confidently wrong
                # and nothing on screen says so.
                wanted_list = list(wanted)
                results = await asyncio.gather(
                    *( _do_one(t) for t in wanted_list ),
                    return_exceptions=True,
                )
                for tgt, res in zip(wanted_list, results):
                    if isinstance(res, asyncio.CancelledError):
                        raise res          # shutdown must still cut through
                    if isinstance(res, BaseException):
                        log.warning(f"translate for {tgt} raised "
                                    f"{type(res).__name__}: {res} — using source text")
                        translated[tgt] = text
                    else:
                        _tgt, out = res
                        translated[tgt] = out or text
                mt_ms = (time.time() - t_mt) * 1000
                # Name the translator that was actually asked, not a fixed
                # string. This line said "Mayura" whichever rung answered, and
                # it cost real time: the mandir PC saw "Mayura" at Gemini
                # latencies, with zero fallbacks logged, and reasonably
                # concluded the briefed translator might not be in the path at
                # all -- during a katha. A label is not evidence, and this one
                # was actively misleading.
                log.info(f"translate fan-out ({effective_translator(GEMINI_API_KEY)}) "
                         f"→ {sorted(wanted)} in {mt_ms:.0f}ms total")
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
                corrected, fired = apply_rules_safely(
                    translated[tgt_lang], active_rules)
                translated[tgt_lang]     = corrected
                fired_by_target[tgt_lang] = fired

            display_corrected = translated.get(target, text)
            # Feed the next line's context. Bounded, and it holds the
            # corrected text — the rules are part of what the hall saw.
            recent_lines.append(display_corrected)
            # Cut computed rather than written as `[:-GEMINI_CONTEXT_LINES]`,
            # which trims nothing at 0 (-0 is 0, so the slice is empty) and so
            # let the list grow for the whole of a 28-hour katha.
            del recent_lines[:max(0, len(recent_lines) - GEMINI_CONTEXT_LINES)]
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
            if state is not None:
                state["last_final_at"] = time.time()

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
                    source_text      = text,
                    source_text_lang = saaras_out,
                )

        # ── Handing a sentence off ───────────────────────────────────
        # Translating goes over the network. The audio sender must NEVER wait
        # on the network: the capture queue holds about four seconds and
        # drops the OLDEST frame when it overflows, so a stalled sender
        # discards speech before it was ever transcribed — absent from the
        # record, with nothing in the output showing a gap. Measured at 3.5s
        # of lost audio against a 7.5s translate (see the test named for it).
        #
        # So nothing awaits the emitter inline. Each release starts its own
        # short-lived task, and a lock keeps them in the order they were
        # spoken — a late caption is a small fault, a caption that overtakes
        # the sentence before it is a confusing one.
        #
        # Deliberately NOT a long-lived consumer task. One was tried twice
        # (a ticker, then a queue-and-publisher) and both perturbed the event
        # loop's ready queue enough to delay the message loop's discovery of
        # a dropped connection. A task that exists only while a caption is in
        # flight costs nothing when nothing is being said.
        publish_lock = asyncio.Lock()
        in_flight: set[asyncio.Task] = set()

        async def _publish(text: str) -> None:
            async with publish_lock:
                await _emit_sentence(text)

        def _publish_finished(task: "asyncio.Task") -> None:
            """Retire the task AND say something if it died.

            `in_flight.discard` alone never retrieved the exception, so a raise
            anywhere in the publish stage produced silence: nothing broadcast,
            the PREVIOUS caption left frozen on the hall screen, and an
            operator debug panel that still looked like a healthy session. The
            translate step is now guarded per target, but everything after it
            — the label, the feed loop, ProPresenter, the recorder — can still
            raise, and a stale caption that reports itself is far cheaper to
            diagnose than one that does not.
            """
            in_flight.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                log.error(
                    f"publishing a caption raised {type(exc).__name__}: {exc} — "
                    f"nothing was broadcast, so the hall is still showing the "
                    f"previous line",
                    exc_info=exc,
                )

        def _hand_off(text: str) -> None:
            """Start publishing a sentence. Never blocks, never awaits."""
            task = asyncio.create_task(_publish(text), name="publish-caption")
            in_flight.add(task)
            task.add_done_callback(_publish_finished)

        async def sender():
            import numpy as np
            log.info("sender: started, waiting for first chunk from audio_gen")
            # Threshold + hangover come from the operator UI (defaults: 1%
            # peak, 1.5 s hangover). Tunable per-session via the Sarvam
            # config row without restarting the server.
            PEAK_THRESHOLD = gate_peak_threshold
            HANGOVER_SEC   = gate_hangover_sec
            LEVEL_BROADCAST_HZ = 4   # ~250 ms cadence to the meter

            sent, skipped, slow_sends, keepalives = 0, 0, 0, 0
            last_report = time.time()
            last_active = 0.0
            # The connection is fresh, so it is idle from now — not from the
            # last time we sent audio on some previous one.
            last_sent = time.time()
            silent_frame = b""
            first_send_logged = False
            silence_streak_logged = False
            level_peak = 0.0
            last_level_broadcast = 0.0

            async def send_frame(payload: bytes) -> None:
                # Per-message `encoding` is a pydantic literal that only
                # accepts "audio/wav". The real codec is at connect-time.
                await ws.transcribe(
                    audio=base64.b64encode(payload).decode(),
                    encoding="audio/wav",
                    sample_rate=16000,
                )

            async for pcm, _ in audio_gen:
                if stop_event.is_set():
                    break

                peak = float(np.abs(np.frombuffer(pcm, dtype=np.int16)).max()) / 32767.0
                if peak > level_peak:
                    level_peak = peak

                now = time.time()

                # The audio frames ARE the heartbeat for sentence assembly.
                # A held sentence must be released when the speaker stops,
                # and the message loop cannot do it: that loop only wakes
                # when Sarvam sends something, and a speaker who has stopped
                # generates nothing to wake it. Frames keep arriving whether
                # or not anyone is speaking, so checking here needs no timer
                # of its own. (A dedicated ticker task was tried and
                # reverted — an extra task in the ready queue measurably
                # delayed the message loop's discovery of a dropped
                # connection, which is a worse fault than a late caption.)
                released = assembler.due(now=now)
                if released:
                    _hand_off(released)

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
                    if keepalive_sec and now - last_sent >= keepalive_sec:
                        # A frame of digital silence, same geometry as the one
                        # we just gated. Holds the connection open without
                        # giving Sarvam anything to transcribe.
                        if len(silent_frame) != len(pcm):
                            silent_frame = bytes(len(pcm))
                        try:
                            await send_frame(silent_frame)
                        except Exception as e:
                            log.error(f"sender: keep-alive ws.transcribe() raised: {e!r}")
                            raise
                        last_sent = now
                        keepalives += 1
                    if not silence_streak_logged and last_active and (now - last_active) > 10:
                        held = (f" Connection held open by a keep-alive every "
                                f"{keepalive_sec:.0f}s." if keepalive_sec else "")
                        log.warning(
                            f"sender: 10s of silence — last loud chunk was "
                            f"{now - last_active:.0f}s ago. Mic gain too low? "
                            f"Threshold = {PEAK_THRESHOLD*100:.1f}% of full-scale."
                            + held
                        )
                        silence_streak_logged = True
                    continue

                sent += 1
                if not first_send_logged:
                    log.info(f"sender: got chunk 1 ({len(pcm)} bytes, peak={peak*100:.1f}%) → ws.transcribe()")
                t_pre = time.time()
                try:
                    await send_frame(pcm)
                except Exception as e:
                    log.error(f"sender: ws.transcribe() raised on chunk {sent}: {e!r}")
                    raise
                last_sent = now
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
                             f"({pct:.0f}% saved, {keepalives} keep-alive) in last "
                             f"{now-last_report:.1f}s "
                             f"(slow_sends={slow_sends}, last_dt={dt*1000:.0f}ms)")
                    sent, skipped, slow_sends, keepalives, last_report = 0, 0, 0, 0, now
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
                        # Hold the fragment until it is a whole sentence.
                        # See SentenceAssembler at module top for why.
                        ready = assembler.add(text, now=time.time())
                        if ready:
                            _hand_off(ready)
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
            # Say the last words of the katha. A session that ends while a
            # sentence is still being assembled must not swallow it.
            tail = assembler.flush()
            if tail:
                _hand_off(tail)
            # Let anything still in flight finish, so the last words of the
            # katha are actually said — but never hang the shutdown on a
            # wedged translator.
            if in_flight:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*in_flight, return_exceptions=True),
                        timeout=SENTENCE_DRAIN_SEC)
                except (asyncio.TimeoutError, Exception):
                    log.warning("gave up waiting for captions still publishing")
                for task in list(in_flight):
                    task.cancel()
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
        "sarvamModels":  SARVAM_MODELS,
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
        audio_gen = audio_from_device(device, state=state)

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
    # Snapshot the speech-service link state. A broadcast only reaches the
    # tabs that were open when it went out, so without this a tab opened —
    # or refreshed — during an outage would show an empty caption bar and no
    # reason for it, which is exactly the ambiguity this state exists to end.
    try:
        await ws.send_str(json.dumps(state.get("connection") or CONNECTION_IDLE))
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


# What each caption surface says it is doing, keyed by the id it reports.
#
# 🔴 This exists because the overlay runs inside vMix's browser input on an
# unattended machine in Bolton, where NOBODY CAN OPEN DEVTOOLS. The overlay
# publishes its state as a DOM attribute, which is readable from a browser and
# useless from a terminal — so the one question worth asking of a hall screen,
# "what do you think you are showing right now?", had no answer from outside.
#
# Asked for by the mandir PC after it deployed and could not report the numbers
# back: "if it were also emitted to the server log or exposed on an HTTP
# endpoint I could report it from here."
#
# Bounded, and last-write-wins per surface: this is a status board, not a log.
_overlay_reports: dict[str, dict] = {}
MAX_OVERLAY_REPORTS = 16

# Past this, capture has stopped being fed at all: the device has gone, been
# taken by another process, or lost its permission. Frames arrive every 500 ms,
# so ten missed frames is not a hiccup.
CAPTURE_STALLED_AFTER_SEC = 5.0

# Past this, audio is arriving but none of it is above the silence gate. A
# katha contains long deliberate pauses, so this is set well beyond any of
# them — the fault it names is a dead or muted mixer feed, which delivers a
# flawless stream of digital silence and leaves every other counter healthy.
CAPTURE_SILENT_AFTER_SEC = 30.0

# Past this, captions have stopped reaching the hall even though audio is
# still being captured — the speech-service link, the translator or the
# publish path. The capture side is not at fault, so the verdict does not
# change; `why` names it, because a 37-second gap with nobody able to say why
# is what this endpoint was widened for.
CAPTION_GAP_WORTH_NAMING_SEC = 20.0

# Past this, a surface is no longer describing anything that exists.
#
# 🔴 Reported by the mandir PC: it closed its test browser and the surface kept
# listing, correctly aged — but its `budget` and `opacity` still read exactly
# like live values. `age_sec` alone makes the reader know the threshold and do
# the arithmetic; someone glancing at a status board under time pressure will
# not. Surfaces report every 5s, so three missed reports is dead.
OVERLAY_STALE_AFTER_SEC = 15.0


async def handle_overlay_report(request: web.Request):
    """POST /api/overlay-report — a caption surface publishing its own state.

    Fire-and-forget from the browser's point of view. It must never be able to
    take a caption surface down, so every failure here is swallowed into a 200:
    a diagnostics channel that can break the thing it reports on is worse than
    no diagnostics channel.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "reason": "unparseable"})

    surface = str(body.get("surface") or "unknown")[:64]
    if surface not in _overlay_reports and len(_overlay_reports) >= MAX_OVERLAY_REPORTS:
        # A page that reloads with a fresh id each time must not grow this
        # without bound. Drop the oldest.
        oldest = min(_overlay_reports, key=lambda k: _overlay_reports[k].get("at", 0))
        _overlay_reports.pop(oldest, None)

    # `shown` is the SEQUENCE of lines that reached the screen; `on_screen` is
    # only the latest. The board reports every 5s and a line changes every ~2s,
    # so a snapshot alone cannot answer "did the whole sentence appear, and in
    # order?" — it will always miss some of them.
    shown = body.get("shown") or []
    if not isinstance(shown, list):
        shown = []

    # 🔴 The lines are kept SEPARATE as well as joined. `on_screen` reads as one
    # sentence, which is what a person wants; but the claim this whole display
    # makes is that a WHOLE line moves up at a time, and a joined string cannot
    # be used to check it — two lines and one long line look identical. The
    # reader diagnosing a half-line on the hall screen needs the boundaries.
    on_screen_lines = body.get("onScreenLines") or []
    if not isinstance(on_screen_lines, list):
        on_screen_lines = []

    _overlay_reports[surface] = {
        "at":              time.time(),
        "state":           body.get("state") or {},
        "on_screen":       str(body.get("onScreen") or "")[:400],
        "on_screen_lines": [str(x)[:200] for x in on_screen_lines[:8]],
        "shown":           [str(x)[:200] for x in shown[-12:]],
    }
    return web.json_response({"ok": True})


def _sec_ago(at: float | None, now: float) -> float | None:
    """Seconds since `at`, or None if it never happened.

    A raw epoch timestamp is not an answer to anyone reading this JSON on a
    phone at the back of a mandir, and the arithmetic is exactly what a
    person under time pressure gets wrong. Clamped at zero because a clock
    that has stepped backwards must not report a negative age as if it meant
    something.
    """
    if at is None:
        return None
    try:
        return round(max(0.0, now - float(at)), 1)
    except (TypeError, ValueError):
        return None


def _queue_depth(q) -> int | None:
    """How many audio chunks are waiting. None when there is no queue."""
    try:
        return int(q.qsize())
    except Exception:
        return None


def _describe_capture(state: dict | None, now: float) -> tuple[dict, str, str]:
    """The input side of the pipeline: (capture block, verdict, why).

    🔴 The caption surfaces can only report what they were GIVEN. If the
    overlay is healthy and the audio has stopped, every field on the surface
    side reads fine and the hall still has no subtitles — that is how a
    37-second caption gap once went undiagnosed from outside. This is the
    half of the board that can tell those two apart.
    """
    state = state if isinstance(state, dict) else {}

    task      = state.get("caption_task")
    task_done = bool(task is not None and getattr(task, "done", lambda: False)())
    running   = bool(state.get("capture_running")) and not task_done

    started_at   = state.get("capture_started_at")
    last_audio   = state.get("last_audio_at")
    last_loud    = state.get("last_loud_audio_at")
    last_final   = state.get("last_final_at")
    audio_ago    = _sec_ago(last_audio, now)
    loud_ago     = _sec_ago(last_loud,  now)
    final_ago    = _sec_ago(last_final, now)

    conn = state.get("connection")
    conn = conn if isinstance(conn, dict) else {}

    device_drops  = state.get("capture_queue_drops") or 0
    session_drops = state.get("session_queue_drops") or 0

    capture = {
        "running":                 running,
        "audio_source":            state.get("audio_source"),
        "device":                  state.get("device"),
        "file":                    state.get("file"),
        "source_lang":             state.get("source"),
        "target_lang":             state.get("target"),
        "running_for_sec":         _sec_ago(started_at, now) if running else None,
        "stopped_sec_ago":         None if running else _sec_ago(state.get("capture_ended_at"), now),
        "link_state":              conn.get("state"),
        "link_reason":             conn.get("reason"),
        "last_audio_sec_ago":      audio_ago,
        "last_loud_audio_sec_ago": loud_ago,
        "last_final_sec_ago":      final_ago,
        # The pump's reading first — it is the only one that survives a link
        # outage. The sender's falls back in for a file replay, where there
        # is no device pump. Written as an explicit None test because the
        # pump resets the key to None at every session start, so `.get` with
        # a default would return that None and never reach the fallback, and
        # a peak of exactly 0.0 is a real reading that must not fall through.
        "last_audio_level":        (state.get("input_peak")
                                    if state.get("input_peak") is not None
                                    else state.get("last_audio_level")),
        "queue_depth":             _queue_depth(state.get("capture_queue")),
        "queue_max":               state.get("capture_queue_max"),
        "dropped_chunks":          device_drops + session_drops,
        "dropped_capture_queue":   device_drops,
        "dropped_session_queue":   session_drops,
        # Stated rather than left implicit: a reader deciding whether 41s is
        # bad should not have to find the thresholds in the source.
        "stalled_after_sec":       CAPTURE_STALLED_AFTER_SEC,
        "silent_after_sec":        CAPTURE_SILENT_AFTER_SEC,
    }

    where = _describe_audio_input(state)

    if not running:
        return capture, "not running", (
            "No capture session is running — nobody has pressed Start, or it "
            "stopped. Nothing can reach the hall screen until it is started."
        )

    ran_for = capture["running_for_sec"]
    if audio_ago is None:
        return capture, "silent", (
            f"Capture has been running {ran_for}s and NOT ONE audio chunk has "
            f"reached the pipeline from {where}. Check the input device is the "
            "right one, is not held by another program, and has microphone "
            "permission."
        )

    if audio_ago > CAPTURE_STALLED_AFTER_SEC:
        return capture, "silent", (
            f"No audio has arrived for {audio_ago}s from {where} (a chunk is "
            f"due every 0.5s). The audio source has stopped delivering — "
            "unplugged, switched off, or taken by another program."
        )

    if loud_ago is None or loud_ago > CAPTURE_SILENT_AFTER_SEC:
        heard = ("nothing above the silence gate since capture started"
                 if loud_ago is None else
                 f"nothing above the silence gate for {loud_ago}s")
        return capture, "silent", (
            f"Audio is arriving from {where} ({audio_ago}s ago) but {heard}. "
            "Either the speaker has stopped, or the feed is muted / the fader "
            "is down — both sound identical from here."
        )

    why = (f"Capturing from {where}: audio {audio_ago}s ago, speech "
           f"{loud_ago}s ago")
    # A link that has just dropped has not yet produced a long caption gap,
    # and it is about to. Say so now rather than in twenty seconds' time.
    if conn.get("state") == "disconnected":
        return capture, "capturing", (
            why + f", but the link to the speech service is DOWN "
            f"({conn.get('reason') or 'no reason given'}, attempt "
            f"{conn.get('attempt')}). The microphone is fine — captions have "
            "stopped because nothing can transcribe them."
        )
    if final_ago is None:
        why += (f", and no caption has been published in the {ran_for}s this "
                "session has been running — speech is being heard but nothing "
                "is coming out of the transcribe/translate path")
    elif final_ago > CAPTION_GAP_WORTH_NAMING_SEC:
        why += (f", but the last caption was {final_ago}s ago. Audio is being "
                f"captured and the link reads "
                f"{conn.get('state') or 'unknown'}, so the fault is after "
                "capture — transcribe, translate or publish. The server log "
                "names which")
    else:
        why += f", last caption {final_ago}s ago"
    return capture, "capturing", why + "."


def _describe_audio_input(state: dict) -> str:
    """The audio input in the words the person on the mandir PC picked it by."""
    source = state.get("audio_source")
    if source == "file":
        return f"file {state.get('file') or '(unnamed)'}"
    device = state.get("device")
    if device in (None, ""):
        return "the default input device"
    return f"input device {device}"


async def handle_overlay_status(request: web.Request):
    """GET /api/overlay-status — is a capture running, and what do the caption
    surfaces say they are showing?

    🔴 `verdict` and `why` are the whole point, and they are first in the
    response on purpose. The reader is a diagnostician on a locked Windows box
    inside vMix who cannot open devtools and cannot read this file, and `curl`
    is the only call they have. They should not have to interpret a single
    other field to know whether to look at the audio or at the screen.

    ⚠️ This must never be able to take the server down. It is a diagnostics
    path on a live production process, and a status endpoint that 500s during
    a fault is worse than no status endpoint — so every field is optional,
    every read is guarded, and a failure to build the answer is reported IN
    the answer rather than raised.
    """
    now = time.time()
    try:
        state = request.app.get("state")
    except Exception:
        state = None

    try:
        capture, verdict, why = _describe_capture(state, now)
    except Exception as e:
        log.error(f"/api/overlay-status: building the capture block raised {e!r}",
                  exc_info=True)
        capture = {"error": f"{type(e).__name__}: {e}"}
        verdict = "unknown"
        why = ("The server could not read its own capture state — this is a "
               "bug in the status endpoint, not necessarily in the capture.")

    surfaces = []
    for name, r in sorted(_overlay_reports.items(),
                          key=lambda kv: kv[1].get("at", 0), reverse=True):
        try:
            at = r.get("at", now)
            surface_state = r.get("state")
            surfaces.append({
                "surface":   name,
                "age_sec":   _sec_ago(at, now),
                # The last values are KEPT rather than blanked — what a surface
                # was showing when it stopped is evidence, and often the most
                # interesting thing on the board.
                "stale":     (now - at) > OVERLAY_STALE_AFTER_SEC,
                "on_screen": r.get("on_screen", ""),
                "on_screen_lines": r.get("on_screen_lines", []),
                "shown":     r.get("shown", []),
                **(surface_state if isinstance(surface_state, dict) else {}),
            })
        except Exception as e:
            # One malformed report must not cost the reader the whole board,
            # least of all the capture verdict above it.
            surfaces.append({"surface": name, "error": f"{type(e).__name__}: {e}"})

    return web.json_response({
        "verdict":  verdict,
        "why":      why,
        "at":       round(now, 1),
        "capture":  capture,
        "surfaces": surfaces,
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

# The header `stop-captions` sends. Its only job is to be something a
# cross-origin <form> cannot set — see the note in handle_shutdown.
SHUTDOWN_HEADER = "X-Captions-Control"
SHUTDOWN_HEADER_VALUE = "shutdown"


async def handle_shutdown(request: web.Request) -> web.Response:
    """Stop the server properly, without Task Manager (issue #61).

    This is the documented way out now that a stray Ctrl+C is refused. It ends
    the process through the same clean path as a normal exit, so the mDNS
    advert is withdrawn, the VOD worker drains and the log records a clean
    stop rather than a hole.

    **Localhost only.** Every other route here is unauthenticated because this
    is a control-room LAN tool, and `/api/stop` already ends captions from
    anywhere on that network. Ending the whole server is a bigger hammer than
    that, and it exists for a script running on the machine itself, so it is
    not offered to the network at all.
    """
    peer = (request.remote or "")
    if peer not in ("127.0.0.1", "::1"):
        log.warning(f"/api/shutdown refused for {peer!r} - localhost only.")
        return web.json_response(
            {"ok": False, "error": "shutdown is localhost-only"}, status=403
        )

    # ⚠️ A localhost check ALONE is not a control, and reviewing this caught it.
    # The operator's own browser is 127.0.0.1. Any web page open on that machine
    # can auto-submit a form-encoded POST to this URL — a "simple request", so
    # no preflight, no consent, no visible sign — and take the katha's captions
    # down. DNS rebinding extends the same trick from off the box.
    #
    # A custom header is the fix, because the one thing a cross-origin <form>
    # cannot do is set one. `fetch` can, but only by asking permission first via
    # a preflight this server never answers. The stop scripts send it.
    if request.headers.get(SHUTDOWN_HEADER, "").strip().lower() != SHUTDOWN_HEADER_VALUE:
        log.warning(
            f"/api/shutdown refused: missing or wrong {SHUTDOWN_HEADER}. "
            f"A browser page cannot set it; stop-captions can."
        )
        return web.json_response(
            {"ok": False, "error": f"{SHUTDOWN_HEADER} header required"}, status=403
        )

    ev = request.app.get("shutdown_event")
    if ev is None:
        return web.json_response(
            {"ok": False, "error": "server has no shutdown event"}, status=500
        )

    log.warning("Shutdown requested over /api/shutdown - stopping cleanly.")

    # Set it on the NEXT turn of the loop, not now: `runner.cleanup()` can win
    # the race against this response being written, and the stop script would
    # then report a failure for a shutdown that worked perfectly.
    asyncio.get_running_loop().call_later(0.25, ev.set)
    return web.json_response({"ok": True, "stopping": True})


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
    # `results/` is gitignored, so a fresh clone does not have it, and
    # aiohttp's add_static() below refuses to start against a directory that
    # does not exist. Existing working copies already had one from earlier
    # runs, which is why this never surfaced until the tool was installed
    # somewhere clean. Mirrors the uploads_dir line a few lines down.
    results_dir.mkdir(parents=True, exist_ok=True)
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
    app.router.add_post("/api/shutdown",              handle_shutdown)
    app.router.add_post("/api/overlay-report",         handle_overlay_report)
    app.router.add_get("/api/overlay-status",          handle_overlay_status)
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
            # The overlay must NEVER be told about this in ink. An unstyled
            # HTML page renders on Chromium's default WHITE background, so
            # vMix would composite a full-screen white panel -- with a shell
            # command printed on it -- over the programme feed, in front of
            # the hall. That is the same fault as the operator boot gate
            # bleeding onto the overlay, one layer further down, and it sits
            # on exactly the path that is most likely to hit it: a build
            # writing dist/ underneath a live browser input.
            #
            # For the overlay the honest failure is an empty transparent
            # stage. The operator still gets the instruction.
            if request.query.get("overlay") == "1":
                return web.Response(
                    status=200, content_type="text/html",
                    text="<!doctype html><html><head><meta charset='utf-8'>"
                         "<style>html,body{background:transparent;margin:0}</style>"
                         "</head><body></body></html>"
                )
            return web.Response(
                status=503, content_type="text/html",
                text="<h1>UI not built yet</h1><p>Run "
                     "<code>cd web &amp;&amp; pnpm install &amp;&amp; pnpm build</code> "
                     "and reload.</p>"
            )
        app.router.add_get("/",          _spa_missing)
        app.router.add_get("/{tail:.*}", _spa_missing)
        log.info(f"React UI: dist not built — / returns a placeholder")

    # The clean way out. A stray Ctrl+C is refused (issue #61), so there has to
    # be a deliberate one, and it has to run the same teardown a normal exit
    # does rather than abandoning the mDNS advert and the VOD worker.
    shutdown_event = asyncio.Event()
    app["shutdown_event"] = shutdown_event
    _install_interrupt_policy(asyncio.get_running_loop(), shutdown_event)

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
        await shutdown_event.wait()   # runs until /api/shutdown or a deliberate Ctrl+C
    except asyncio.CancelledError:
        pass
    finally:
        await unregister_mdns_service(mdns_handle)
        await vod_jobs.shutdown()
        await feed_registry.shutdown()
        await runner.cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# Surviving a stray Ctrl+C (issue #61)
# ─────────────────────────────────────────────────────────────────────────────
#
# The server was killed by a console control event twice on 3 September, not by
# a crash: Task Scheduler recorded 0xC000013A (STATUS_CONTROL_C_EXIT) and the
# log ends in `^C` immediately after healthy lines. Nobody knows who raised it,
# and the most likely explanation needs no malice — `start-captions.bat` runs
# the server in a visible console, and clicking that window and pressing Ctrl+C
# to COPY a line out of it is a console control event.
#
# There is deliberately no attempt to detect whether an operator is watching.
# #61 requires the check to fail toward STAYING UP, and every available signal
# fails the other way: under `start-captions.bat` the process owns a real
# console, so `isatty()` reports an interactive terminal and hands back exactly
# the behaviour that caused the outage. Counting presses needs no such guess —
# one press is always a stray, and a burst is always someone meaning it.

def _install_interrupt_policy(loop, shutdown_event) -> None:
    """Point SIGINT (and Ctrl+Break on Windows) at the policy.

    The handler never raises. Raising `KeyboardInterrupt` out of a signal
    handler is what used to end the process, and it skips the teardown — so a
    deliberate exit sets the same event `/api/shutdown` sets and leaves through
    the ordinary door.

    ⚠️ **A closed console window cannot be caught here.** Windows delivers
    `CTRL_CLOSE_EVENT` for that, Python does not surface it as a signal, and the
    OS kills the process a few seconds later regardless. Refusing Ctrl+C does
    not make the window safe to close, and `WINDOWS.md` says so.
    """
    # ⚠️ Parsed defensively on purpose. #61 requires this to fail toward STAYING
    # UP, and `int()` on a blank or mistyped .env line raises inside main() —
    # which would mean a stray keystroke in a config file stops the server
    # booting at all. The worst a bad value may cost is the default.
    raw = os.environ.get("CAPTION_INTERRUPT_PRESSES", "3").strip()
    try:
        escape_presses = int(raw)
        if escape_presses < 0:
            raise ValueError("negative")
    except ValueError:
        log.error(
            f"CAPTION_INTERRUPT_PRESSES={raw!r} is not a whole number - "
            f"using the default of 3."
        )
        escape_presses = 3

    policy = InterruptPolicy(escape_presses=escape_presses)

    def _handle(signum, frame):
        decision = policy.on_interrupt()
        if decision.should_exit:
            log.warning(decision.message)
            loop.call_soon_threadsafe(shutdown_event.set)
        else:
            # ERROR, not WARNING: in the hall this line is the trace that an
            # outage was attempted and refused, and #61 asks for exactly that.
            log.error(decision.message)

    for name in ("SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError) as exc:
            # Not the main thread, or a platform that will not take it. The
            # server still runs; it is just killable the old way.
            log.warning(f"Could not install a {name} handler: {exc}")


@dataclass(frozen=True)
class InterruptDecision:
    """What to do about a console control event, and what to say about it."""

    should_exit: bool
    message: str
    presses_in_window: int


class InterruptPolicy:
    """Refuses a stray interrupt; obeys a deliberate one.

    `escape_presses` interrupts inside `escape_window_sec` mean the person at
    the keyboard meant it. Anything less is ignored and reported. Set
    `escape_presses=0` and no key combination can end the process — the
    documented stop path is `stop-captions` either way, so this is not the same
    as being unkillable.
    """

    def __init__(
        self,
        clock=time.monotonic,
        escape_presses: int = 3,
        escape_window_sec: float = 2.0,
    ):
        self._clock = clock
        self._escape_presses = escape_presses
        self._window = escape_window_sec
        self._presses: list[float] = []

    def on_interrupt(self) -> InterruptDecision:
        now = self._clock()

        # Only presses still inside the window count. An operator copying a
        # line out of the console once an hour must never accumulate their way
        # into stopping the captions.
        self._presses = [t for t in self._presses if now - t < self._window]
        self._presses.append(now)
        n = len(self._presses)

        if self._escape_presses > 0 and n >= self._escape_presses:
            return InterruptDecision(
                should_exit=True,
                message=(
                    f"Ctrl+C {n} times in {self._window:g}s - stopping, as asked."
                ),
                presses_in_window=n,
            )

        stop_hint = (
            "Use stop-captions (or POST /api/shutdown) to stop the server."
        )
        if self._escape_presses <= 0:
            tail = f"{stop_hint} Ctrl+C cannot end it on this machine."
        else:
            remaining = self._escape_presses - n
            tail = (
                f"{stop_hint} Press Ctrl+C {remaining} more "
                f"{'time' if remaining == 1 else 'times'} within "
                f"{self._window:g}s to exit anyway."
            )

        return InterruptDecision(
            should_exit=False,
            message=f"Ctrl+C ignored - captions stay up. {tail}",
            presses_in_window=n,
        )


def entry():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--propresenter", action="store_true",
                   help="Also push captions to ProPresenter Messages overlay")
    p.add_argument("--log-dir", default=None,
                   help=f"Where to write {LOG_FILENAME} (default: logs/ next to "
                        f"this file; ${LOG_DIR_ENV} also works)")
    args = p.parse_args()

    log_path = configure_logging(args.log_dir)

    # A banner, so a log covering a fortnight can be cut at the restarts.
    # Reconstructing outage 1 meant reading Task Scheduler for the times
    # this line would have printed for free.
    log.info("=" * 64)
    log.info(f"Live captions starting - pid {os.getpid()}, port {args.port}")
    if log_path:
        log.info(f"Log file: {log_path}")
    else:
        log.warning(
            "NO WRITABLE LOG DIRECTORY - this session logs to the console only, "
            f"and the log dies with the window. Set {LOG_DIR_ENV} to a writable path."
        )

    # Every way this process can end, named in the log, because the whole
    # point of the file is that the next reader does not have to guess.
    outcome = "unknown"
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        # On Windows a console close arrives here too.
        outcome = "Ctrl+C"
        log.warning("Stopped by Ctrl+C or a closed console (KeyboardInterrupt).")
    except Exception:
        # Without this the traceback goes to stderr — which the file handler
        # is not attached to, and which under the logon task's hidden window
        # goes nowhere at all. The `finally` line below would then print, and
        # a crash log would end looking exactly like a clean shutdown. The
        # hand-typed `2> ...err.log` redirect this replaces did catch
        # tracebacks; replacing it with something worse would not be a fix.
        outcome = "CRASHED"
        log.exception("CRASHED - the server loop raised:")
    else:
        outcome = "clean"
        log.info("Stopped: the server loop returned.")
    finally:
        # No logging.shutdown() here — atexit already runs it, and the file
        # handler flushes on every record anyway. That flush is the property
        # that matters: nothing about the 3 September outages exited cleanly.
        log.info(f"Live captions exiting - pid {os.getpid()} - {outcome}")

    # Non-zero so Task Scheduler's LastTaskResult tells the truth as well.
    # A crash that reports success is how a machine ends up believed healthy.
    if outcome == "CRASHED":
        raise SystemExit(1)


if __name__ == "__main__":
    entry()
