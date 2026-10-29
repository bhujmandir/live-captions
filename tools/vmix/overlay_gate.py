"""Let the desk's overlay button start and stop the captions.

The desk turns captions on and off with vMix's overlay 2 button on the
`Captions Overlay` input. This gate follows that button, and nothing else:

  * ON   -- overlay 2 carries the `Captions Overlay` input. If nothing is
            capturing, start a capture with the hall's speech settings.
  * OFF  -- overlay 2 carries anything else, or nothing, for the stop grace
            (10 s) in a row: stop the capture. If the button comes back on
            within the grace, the stop is cancelled, so a quick flick of the
            button does not tear down the Sarvam session.

And one thing for the screen rather than the capture:

  * BLANK -- overlay 2 is ON but no overlay surface has reported to the server
            for the navigate grace (15 s): tell vMix to navigate the input to
            the agreed overlay URL. vMix's browser input loads its page once
            and never tries again, so a vMix opened before the server stayed
            blank for an hour on 11 Oct. Navigate rather than reload: a reload
            keeps whatever URL vMix restored from its preset. A surface
            that still has not reported is navigated again after 30 s, then
            every minute; a surface that is reporting is never touched.

The input is found by its TITLE (or KEY) every poll, never by number: input
numbers moved four times in katha week. The audio device goes to the server
by NAME for the same reason (device numbers moved 6 -> 1): the server finds
today's number for it, or refuses the start.

Deliberately conservative:
  * vMix unreachable or unreadable (closed, restarting, busy) is NOT "off".
    Nothing happens, and any pending stop is cancelled.
  * The captions server not answering is no reason to navigate: no page can
    load from it, and an overlay that was showing reconnects by itself.
  * A start is only sent once the server reports `capture.running: false`
    (a stop followed at once by a start answers "Already running" and leaves
    nothing capturing -- 6 Oct).
  * Stream and record state are ignored: the button is the only signal.

The hall's settings (input title and key, overlay number, device, speech
settings, graces, overlay URL) come from `overlay-gate.json` in the checkout,
next to `.env`. A missing file, or a missing key, means the default below;
`overlay-gate.example.json` lists every key. The log says which.

`Gate.step()` is the whole behaviour: one look at vMix and at the server,
and the requests that follow from it. `main()` repeats it once a second.
Importing this module starts nothing.

One line per transition goes to logs/overlay-gate.log in the checkout, rolled
over at midnight and kept for two weeks, and to stdout when there is one.

Runs on the mandir PC from a logon Scheduled Task (`LiveCaptionsOverlayGate`:
`.venv/Scripts/pythonw.exe tools/vmix/overlay_gate.py`, restart on failure).
Proven off air on 7 Oct; the desk's only control is the button.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import logging.handlers
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# tools/vmix/overlay_gate.py -> the checkout.
CHECKOUT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = CHECKOUT / "overlay-gate.json"
LOG_DIR = CHECKOUT / "logs"
LOG_FILENAME = "overlay-gate.log"
LOG_RETAIN_DAYS = 14
LOGGER = "overlay_gate"


@dataclass(frozen=True)
class Settings:
    """One hall's settings. The defaults name no hall: a hall's own values
    (its audio input, say) go in its overlay-gate.json."""
    input_title: str = "Captions Overlay"
    # Fallback if the input is renamed: that input's vMix key (from the API
    # XML). Empty means match by title only.
    input_key: str = ""
    overlay: int = 2
    # The capture device's name as the driver reports it: the operator page
    # shows it with " — <host API>" after it. Sent to the server by name.
    # Empty sends no device: the server captures from its default input.
    device_name: str = ""
    # Empty means any host API.
    device_host_api: str = ""
    # The language direction, as the server's codes (`gu-IN`, `en-IN`). Empty
    # sends none: the server keeps the direction it has (`.env`'s default, or
    # the operator page's last choice).
    source_lang: str = ""
    target_lang: str = ""
    speech: dict = field(default_factory=lambda: {
        "model": "saaras:v4", "force_flush_sec": 4,
        "min_coverage": 0.4, "lag_limit_sec": 20})
    stop_grace_sec: float = 10.0
    # Overlay on and no overlay surface reporting for this long: navigate the
    # input to `overlay_url`.
    navigate_grace_sec: float = 15.0
    # Where vMix's captions input should point. Only the query vocabulary
    # frozen by docs/adr/0002 belongs in it.
    # The agreed overlay URL of WINDOWS.md.
    overlay_url: str = ("http://127.0.0.1:8765/?overlay=1&lines=2&fs=64&fw=600"
                        "&tbg=25&panel=1&ailabel=1")
    vmix_api: str = "http://127.0.0.1:8088/api/"
    captions_server: str = "http://127.0.0.1:8765"
    poll_sec: float = 1.0


DEFAULTS = Settings()

# The query names the overlay reads (web/src/settings.ts, OverlayView.tsx, main.tsx),
# frozen by docs/adr/0002: the URL lives in a vMix preset, not in this repo.
OVERLAY_URL_VOCABULARY = frozenset({
    "overlay", "lines", "area", "block", "noblock", "fs", "fw", "bg", "ff",
    "fit", "tbg", "panel", "ailabel", "dwell", "maxdwell", "clearafter", "scale",
})


def _overlay_url_problem(url: str) -> str | None:
    """What is wrong with `url` as the overlay's URL, or None if nothing is."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query,
                                  keep_blank_values=True)
    unknown = sorted(set(query) - OVERLAY_URL_VOCABULARY)
    if unknown:
        return (f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} "
                "not in the overlay URL's vocabulary (docs/adr/0002)")
    if query.get("overlay") != ["1"]:
        # Without it the URL is the operator page, whose surface never reports
        # as the overlay: the gate would navigate to it for ever.
        return "it has no overlay=1, so it is not the overlay"
    return None


def _as_kind_of(default, value):
    """`value` as the same kind of thing as `default`, or None if it isn't one.

    JSON has one kind of number, so 2.0 is a fine overlay number and 10 a fine
    grace; but 2.5 is no overlay number, and True is no number at all.
    """
    if isinstance(value, bool) or isinstance(default, bool):
        return value if type(value) is type(default) else None
    if isinstance(default, (int, float)):
        if not isinstance(value, (int, float)):
            return None
        if isinstance(default, int) and not float(value).is_integer():
            return None
        return type(default)(value)
    return value if isinstance(value, type(default)) else None


def load_settings(path: Path, say) -> Settings:
    """The hall's settings from `path`, the defaults for anything it lacks.

    Never raises: a gate on the defaults beats no gate at a katha. Every
    fallback is said in the log, so a typo in the file is visible afterwards.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        say(f"no settings file at {path}: using the defaults")
        return DEFAULTS
    except (OSError, ValueError) as e:
        say(f"could not read settings file {path} ({type(e).__name__}: {e}): "
            "using the defaults")
        return DEFAULTS
    if not isinstance(raw, dict):
        say(f"settings file {path} is not a JSON object: using the defaults")
        return DEFAULTS

    names = {f.name for f in dataclasses.fields(Settings)}
    for k in sorted(set(raw) - names):
        say(f"settings file {path}: unknown setting '{k}' ignored")

    chosen: dict = {}
    for name in sorted(names & set(raw)):
        default, value = getattr(DEFAULTS, name), raw[name]
        if name == "speech" and isinstance(value, dict):
            value = {**default, **value}
        kept = _as_kind_of(default, value)
        if kept is None:
            say(f"settings file {path}: '{name}' should be like {default!r}, "
                f"got {value!r}: using the default")
            continue
        if name == "overlay_url" and (problem := _overlay_url_problem(kept)):
            say(f"settings file {path}: 'overlay_url' {kept!r}: {problem}: "
                "using the default")
            continue
        chosen[name] = kept

    defaulted = sorted(names - set(chosen))
    say(f"settings from {path}"
        + (f"; defaults for {', '.join(defaulted)}" if defaulted else ""))
    return dataclasses.replace(DEFAULTS, **chosen)


# ── What vMix says ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OverlayReading:
    """What one read of vMix's API says about the captions input.

    `on` is True/False when vMix answered, None when it could not be read.
    `input_key` is the captions input's vMix key, when it was found.
    """
    on: bool | None
    why: str = ""
    input_key: str = ""


def read_overlay(vmix, settings: Settings) -> OverlayReading:
    try:
        root = ET.fromstring(vmix.get())
    except Exception as e:
        return OverlayReading(None, why=f"{type(e).__name__}: {e}")
    inputs = root.find("inputs")
    overlays = root.find("overlays")
    if inputs is None or overlays is None:
        return OverlayReading(None, why="no inputs or overlays in vMix's answer")
    found = next((i for i in inputs if i.get("title") == settings.input_title), None)
    if found is None and settings.input_key:
        found = next((i for i in inputs if i.get("key") == settings.input_key), None)
    if found is None:
        # vMix is up but the preset has not loaded the input (yet).
        return OverlayReading(None, why=f"no '{settings.input_title}' input")
    key = found.get("key") or ""
    for o in overlays:
        if o.get("number") == str(settings.overlay):
            return OverlayReading((o.text or "").strip() == found.get("number"),
                                  input_key=key)
    return OverlayReading(False, input_key=key)


# ── The gate ────────────────────────────────────────────────────────────────

START = "start"
STOP = "stop"
NAVIGATE = "navigate"

# The longest wait between navigates that do not bring the surface back.
NAVIGATE_BACKOFF_MAX_SEC = 60.0
# The shortest, whatever the grace: the overlay reports every 5 s.
NAVIGATE_GAP_MIN_SEC = 5.0


def _overlay_reporting(status: dict) -> bool:
    """Has an overlay surface reported within the server's staleness window?

    The overlay reports as `overlay-<random>`, every 5 s. The operator page's
    surface (`operator-...`) reports too, and says nothing about vMix's input.
    """
    return any(isinstance(x, dict) and str(x.get("surface", "")).startswith("overlay-")
               and x.get("stale") is False
               for x in status.get("surfaces") or [])


def _surfaces_seen(status: dict) -> str:
    """The surfaces the server lists, for the log: `overlay-k3x9qa stale, ...`."""
    seen = [str(x.get("surface")) + (" stale" if x.get("stale") else "")
            for x in status.get("surfaces") or [] if isinstance(x, dict)]
    return "surfaces: " + ", ".join(seen) if seen else "no surface reporting at all"


def _device_for_log(s: Settings) -> str:
    """The device a start asks for, for the log."""
    if not s.device_name:
        return "the server's default input"
    return f"'{s.device_name}' ({s.device_host_api or 'any host API'})"


class Gate:
    """Follows the overlay button. `step()` is called once per poll.

    A step is one read of vMix and one of the server, `decide()` turning the
    two into actions, and the requests that carry them out.
    """

    def __init__(self, settings: Settings, vmix, server, say):
        self.settings = settings
        self.vmix = vmix
        self.server = server
        self.say = say
        self.last_seen: bool | None = None   # last definite overlay state logged
        self.off_since: float | None = None  # when the current OFF stretch began
        self.vmix_down = False
        self.server_down = False
        self.last_error: dict[str, str] = {}  # per action, the last fault logged
        # When the current stretch of ON with no overlay surface reporting began.
        self.missing_since: float | None = None
        self.navigate_at = 0.0               # the earliest next navigate
        self.navigates_sent = 0              # since `missing_since`
        self.navigate_why = ""               # for the log line of the next one
        self.input_key = ""                  # the captions input's, last read

    def step(self, now: float) -> None:
        reading = read_overlay(self.vmix, self.settings)
        # With vMix unreadable nothing will be done, so the server is not asked.
        status = self._status() if reading.on is not None else None
        for action in self.decide(reading, status, now):
            self._carry_out(action, now)

    def decide(self, reading: OverlayReading, status: dict | None,
               now: float) -> list[str]:
        """The actions that follow from what vMix and the server say.

        `status` is the server's /api/overlay-status, or None when it did not
        answer. Logs each transition as it sees it.
        """
        s = self.settings
        if reading.on is None:
            if not self.vmix_down:
                self.say("vMix unreadable (closed or loading): doing nothing"
                         + (", pending stop cancelled" if self.off_since else "")
                         + f" [{reading.why}]")
                self.vmix_down = True
            self.off_since = None
            self.missing_since = None
            return []
        if self.vmix_down:
            self.say("vMix readable again")
            self.vmix_down = False

        on = reading.on
        if on != self.last_seen:
            self.say(f"overlay {s.overlay} {'ON' if on else 'OFF'} ('{s.input_title}' "
                     f"{'on' if on else 'not on'} channel {s.overlay})")
            self.last_seen = on

        if on and self.off_since is not None:
            self.say(f"back on after {now - self.off_since:.1f}s: stop cancelled")
            self.off_since = None
        if not on and self.off_since is None:
            self.off_since = now

        if status is None:
            # A surface that was showing reports again within 5 s of the
            # server's return: the grace starts then, not before.
            self.missing_since = None
            return []
        actions = []
        capturing = bool(status["capture"]["running"])
        if on and not capturing:
            actions.append(START)
        if not on and capturing and now - self.off_since >= s.stop_grace_sec:
            actions.append(STOP)
        self.input_key = reading.input_key
        if on and not _overlay_reporting(status):
            if self.missing_since is None:
                self.missing_since = now
                self.navigates_sent = 0
                self.navigate_at = now + s.navigate_grace_sec
            if now >= self.navigate_at:
                actions.append(NAVIGATE)
                self.navigate_why = (
                    f"overlay {s.overlay} ON but no overlay surface has reported for "
                    f"{now - self.missing_since:.0f}s ({_surfaces_seen(status)})")
                # The surface needs the grace to load and report. One that
                # still has not after that is waited for longer each time.
                self.navigates_sent += 1
                wait = min(s.navigate_grace_sec * 2 ** self.navigates_sent,
                           NAVIGATE_BACKOFF_MAX_SEC)
                self.navigate_at = now + max(wait, s.navigate_grace_sec,
                                             NAVIGATE_GAP_MIN_SEC)
        else:
            if self.missing_since is not None and self.navigates_sent and on:
                self.say(f"overlay surface reporting again after {self.navigates_sent} "
                         f"navigate{'s' if self.navigates_sent > 1 else ''}")
            self.missing_since = None
        return actions

    def _carry_out(self, action: str, now: float) -> None:
        try:
            if action == START:
                self.say(f"capture start: {self._start()}")
            elif action == STOP:
                self.say(f"off for {now - self.off_since:.1f}s: "
                         f"capture stop: {self._stop()}")
            elif action == NAVIGATE:
                self.say(f"{self.navigate_why}: {self._navigate()}")
            self.last_error.pop(action, None)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            # Once per distinct fault, not once a second.
            if err != self.last_error.get(action):
                what = ("navigate '" + self.settings.input_title + "'"
                        if action == NAVIGATE else "change capture state")
                self.say(f"could not {what}: {err} (will retry)")
                self.last_error[action] = err

    # ── the captions server ──

    def _status(self) -> dict | None:
        """/api/overlay-status, or None when the server is not answering."""
        try:
            status = self.server.get("/api/overlay-status")
            status["capture"]["running"]   # an answer without it is no answer
        except Exception as e:
            if not self.server_down:
                self.say(f"captions server not answering ({type(e).__name__}: {e}): "
                         "doing nothing until it does")
                self.server_down = True
            return None
        if self.server_down:
            self.say("captions server answering again")
            self.server_down = False
        return status

    def _start(self) -> str:
        s = self.settings
        speech = dict(s.speech)
        body = {"source": "device", "sarvam": speech, "gate": {}}
        if s.device_name:
            body["device_name"] = s.device_name
            if s.device_host_api:
                body["device_host_api"] = s.device_host_api
        # Only what the hall set: anything sent overrides the server's own.
        if s.source_lang:
            body["source_lang"] = s.source_lang
        if s.target_lang:
            body["target_lang"] = s.target_lang
        dev = _device_for_log(s)
        code, text = self.server.post("/api/start", body)
        if code == 409:
            # The last capture has not finished tearing down. The next poll
            # sees `running` again and tries once it is really stopped.
            return f"device {dev} sarvam={speech} -> 409 {text} (will retry)"
        if code >= 400:
            # No device of that name today, say. Logged once, tried each poll.
            raise RuntimeError(f"start refused ({code}): {text}")
        return f"device {dev} sarvam={speech} -> {text}"

    def _stop(self) -> str:
        code, text = self.server.post("/api/stop", {})
        return "(already stopped)" if code == 409 else text

    # ── vMix ──

    def _navigate(self) -> str:
        s = self.settings
        # BrowserNavigate silently refuses a URL with a comma in it;
        # the overlay reads %2C back as a comma.
        answer = self.vmix.call("BrowserNavigate",
                                Input=self.input_key or s.input_title,
                                Value=s.overlay_url.replace(",", "%2C"))
        return f"BrowserNavigate '{s.input_title}' -> {s.overlay_url}: {answer}"


# ── The real clients ────────────────────────────────────────────────────────

# Both peers are on this PC. A proxy set for the browser must not sit between.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class VmixApi:
    """vMix's HTTP API: a GET answers with the state XML, a GET with
    `?Function=` carries out that function."""

    def __init__(self, base: str, timeout: float = 4):
        self.base, self.timeout = base, timeout

    def get(self) -> bytes:
        with _opener.open(self.base, timeout=self.timeout) as r:
            return r.read()

    def call(self, function: str, **params: str) -> str:
        """Carry out one vMix function. vMix answers an error with a 500,
        which raises here."""
        query = urllib.parse.urlencode({"Function": function, **params})
        with _opener.open(f"{self.base}?{query}", timeout=self.timeout) as r:
            return r.read().decode("utf-8", "replace").strip()


class CaptionsServer:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def get(self, path: str, timeout: float = 5) -> dict:
        with _opener.open(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def post(self, path: str, payload: dict, timeout: float = 15) -> tuple[int, str]:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with _opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as e:
            # An answer, not an outage: the gate decides what a 409 means.
            return e.code, e.read().decode("utf-8", "replace").strip()


# ── The log ─────────────────────────────────────────────────────────────────

class _Stdout(logging.Handler):
    """stdout as UTF-8 whatever the console's code page (a "₹" kills cp1252)."""

    def emit(self, record):
        try:
            line = self.format(record) + "\n"
            out = sys.stdout
            if out is None:
                return
            if hasattr(out, "buffer"):
                out.buffer.write(line.encode("utf-8", "replace"))
            else:
                out.write(line)
            out.flush()
        except Exception:
            self.handleError(record)


def configure_log(log_dir: Path = LOG_DIR) -> Path | None:
    """One line per transition, to a file rolled at midnight and to stdout.

    Returns the file, or None when nothing was writable: the gate then runs
    without a file rather than not at all.
    """
    log = logging.getLogger(LOGGER)
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Under pythonw (the logon task) there is no console and stdout is None.
    if sys.stdout is not None:
        out = _Stdout()
        out.setFormatter(fmt)
        log.addHandler(out)

    path = Path(log_dir) / LOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.TimedRotatingFileHandler(
            path, when="midnight", backupCount=LOG_RETAIN_DAYS, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return path


# ── The entry point ─────────────────────────────────────────────────────────

def run(gate: Gate, poll_sec: float, *, steps: int | None = None) -> None:
    """Repeat the gate's step forever (or `steps` times)."""
    n = 0
    while steps is None or n < steps:
        if n:
            time.sleep(poll_sec)
        try:
            gate.step(time.time())
        except Exception as e:
            # A bug in one step must not take the button away for the katha.
            gate.say(f"gate step failed: {type(e).__name__}: {e} (carrying on)")
        n += 1


def main(argv: list[str] | None = None, *, steps: int | None = None) -> None:
    p = argparse.ArgumentParser(description="Follow vMix's overlay button: "
                                "on starts the captions, off for the grace stops them.")
    p.add_argument("--settings", type=Path, default=SETTINGS_PATH,
                   help=f"the hall's settings file (default: {SETTINGS_PATH.name} "
                   "in the checkout)")
    p.add_argument("--log-dir", type=Path, default=LOG_DIR,
                   help="where overlay-gate.log goes (default: logs/ in the checkout)")
    args = p.parse_args(argv)

    path = configure_log(args.log_dir)
    say = logging.getLogger(LOGGER).info
    if path is None:
        say(f"could not open a log file in {args.log_dir}: logging to the console only")
    s = load_settings(args.settings, say)
    if not s.device_name:
        say(f"no device_name set: using the server's default input. Set the hall's "
            f"input in {args.settings.name} (see WINDOWS.md)")
    say(f"overlay gate armed: follows overlay {s.overlay} carrying '{s.input_title}', "
        f"start at once, stop after {s.stop_grace_sec:g}s off; device "
        f"{_device_for_log(s)}, speech {s.speech}")

    gate = Gate(s, VmixApi(s.vmix_api), CaptionsServer(s.captions_server), say)
    run(gate, s.poll_sec, steps=steps)


if __name__ == "__main__":
    main()
