"""Test doubles for driving the overlay gate's single step.

Two pieces, one for each thing the gate talks to:

* `FakeVmix` — vMix's HTTP API. Answers with canned XML for the input list
  and the overlay channels, or refuses the connection the way a closed or
  restarting vMix does. Counts its reads, and records every function call
  (a `BrowserNavigate`, say) so a test can check what the gate told vMix.
* `FakeCaptionsServer` — the captions server. Answers `/api/overlay-status`
  with the caption surfaces a test gives it, takes `/api/start` and
  `/api/stop`, and records every request so a test can check exactly what
  the gate asked for. A start by device name is looked up
  in its device list by the server's own rule, and refused when the name is
  not there. A stop can be made slow, the way a real capture takes a moment
  to tear down.

Both have the same small surface as the gate's real clients (`get`, and
`post` or `call`), raising `OSError` when nothing is listening.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import live_captions

CAPTIONS_KEY = "5d3c7e2a-captions"
CAPTIONS_NUMBER = "34"

def device(index: int, name: str, host_api: str, works: bool = True) -> dict:
    """One entry as the server's `/api/devices` lists it."""
    return {"id": str(index), "name": f"{name} — {host_api}", "deviceName": name,
            "hostApi": host_api, "worksAt16k": works, "default": False}


def surface(name: str, *, stale: bool = False) -> dict:
    """One caption surface as `/api/overlay-status` lists it. The overlay page
    reports as `overlay-<random>`, the operator page as `operator-<random>`."""
    return {"surface": name, "age_sec": 40.0 if stale else 2.0, "stale": stale,
            "on_screen": "", "on_screen_lines": [], "shown": []}


# What `/api/devices` lists on the mandir PC: the hall's Line In is not the
# first entry, and its index is not the one it had last week.
HALL_DEVICES = [
    device(0, "Microsoft Sound Mapper - Input", "MME"),
    device(1, "Line In (Realtek(R) Audio)", "MME"),
    device(5, "Stereo Mix (Realtek(R) Audio)", "MME"),
    device(9, "Line In (Realtek(R) Audio)", "Windows WASAPI", works=False),
]


def vmix_xml(overlay2: str | None, *, captions_input: bool = True,
             title: str = "Captions Overlay") -> bytes:
    """vMix's API answer. `overlay2` is the input NUMBER on overlay 2, or None."""
    inputs = ['<input key="0a1b-logo" number="1" type="Image" title="Logo">Logo</input>']
    if captions_input:
        inputs.append(f'<input key="{CAPTIONS_KEY}" number="{CAPTIONS_NUMBER}" '
                      f'type="Browser" title="{title}">{title}</input>')
    ch2 = (f'<overlay number="2">{overlay2}</overlay>' if overlay2
           else '<overlay number="2" />')
    return (
        "<vmix><version>26.0.0.45</version>"
        f"<inputs>{''.join(inputs)}</inputs>"
        f'<overlays><overlay number="1">1</overlay>{ch2}'
        '<overlay number="3" /><overlay number="4" /></overlays>'
        "</vmix>"
    ).encode("utf-8")


ON = vmix_xml(CAPTIONS_NUMBER)
OFF = vmix_xml(None)
SOMETHING_ELSE_ON = vmix_xml("1")


class FakeVmix:
    def __init__(self, xml: bytes | None = ON):
        # None = nothing listening on the API port.
        self.xml = xml
        self.reads = 0
        self.calls: list[dict] = []
        # vMix's error text for every function call (a 500), or None.
        self.refuse_calls: str | None = None

    def get(self) -> bytes:
        self.reads += 1
        if self.xml is None:
            raise ConnectionRefusedError("vMix is not listening")
        return self.xml

    def call(self, function: str, **params: str) -> str:
        if self.xml is None:
            raise ConnectionRefusedError("vMix is not listening")
        self.calls.append({"Function": function, **params})
        if self.refuse_calls:
            raise OSError(self.refuse_calls)
        return "Function completed successfully."

    @property
    def navigates(self) -> list[dict]:
        return [c for c in self.calls if c["Function"] == "BrowserNavigate"]


@dataclass
class FakeCaptionsServer:
    running: bool = False
    devices: list = field(default_factory=lambda: list(HALL_DEVICES))
    answering: bool = True
    # How many status reads still say `running: true` after a stop arrives.
    stop_takes_reads: int = 0
    # How many starts are refused as "Already running" although the status
    # says stopped: the moment between the flag clearing and the server
    # letting go of the last capture.
    refuse_starts: int = 0
    requests: list = field(default_factory=list)
    # The device index the capture is reading from, as the server reports it.
    capturing_from: str | None = None
    # The caption surfaces `/api/overlay-status` lists: see `surface()`.
    surfaces: list = field(default_factory=list)
    _stopping_reads: int = 0

    def get(self, path: str) -> dict:
        self._check_answering()
        self.requests.append(("GET", path, None))
        if path == "/api/overlay-status":
            if self._stopping_reads:
                self._stopping_reads -= 1
                if not self._stopping_reads:
                    self.running = False
            return {"verdict": "ok",
                    "capture": {"running": self.running,
                                "device": self.capturing_from if self.running else None},
                    "surfaces": list(self.surfaces)}
        raise AssertionError(f"the gate asked for an unexpected path {path!r}")

    def post(self, path: str, payload: dict) -> tuple[int, str]:
        self._check_answering()
        self.requests.append(("POST", path, payload))
        if path == "/api/start":
            if self.running or self.refuse_starts:
                self.refuse_starts = max(0, self.refuse_starts - 1)
                return 409, "Already running"
            dev = payload.get("device")
            if payload.get("device_name"):
                found = live_captions.resolve_device(
                    self.devices, payload["device_name"],
                    payload.get("device_host_api") or "")
                if found is None:
                    return 400, live_captions.no_device_named(
                        payload["device_name"], payload.get("device_host_api") or "")
                dev = found["id"]
            self.running, self.capturing_from = True, dev
            return 200, '{"status": "started"}'
        if path == "/api/stop":
            if self.stop_takes_reads:
                self._stopping_reads = self.stop_takes_reads
            else:
                self.running = False
            return 200, '{"status": "stopped"}'
        raise AssertionError(f"the gate posted to an unexpected path {path!r}")

    @property
    def starts(self) -> list[dict]:
        return [body for verb, path, body in self.requests
                if verb == "POST" and path == "/api/start"]

    @property
    def stops(self) -> int:
        return sum(1 for verb, path, _ in self.requests
                   if verb == "POST" and path == "/api/stop")

    def _check_answering(self):
        if not self.answering:
            raise ConnectionRefusedError("captions server is not listening")
