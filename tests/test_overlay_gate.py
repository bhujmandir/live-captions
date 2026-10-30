"""The desk's overlay button starts and stops the captions.

Written against katha week, 5–11 Oct 2026. The desk turns captions on
and off with vMix's overlay 2 button and nothing else, so the gate that
follows that button is the only thing standing between the desk and a
silent screen. These are the properties the hall depended on:

  * overlay 2 carrying the captions input starts a capture, on the hall's
    speech settings (F8: on 6 Oct evening nobody had started one);
  * overlay 2 off for the grace stops it, and a quick flick of the button
    does not tear down the speech link;
  * vMix being closed, restarting or busy is never read as "off";
  * a start waits until a stopped capture has really stopped (6 Oct: a stop
    then an immediate start answered "Already running" and left nothing
    capturing).

Each test drives the gate's single step against a fake vMix and a fake
captions server, and checks the requests the gate sent.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import logging.handlers
import re
import sys
import urllib.parse

import pytest

from tools.vmix import overlay_gate

from .fakes_gate import (
    CAPTIONS_KEY, OFF, ON, SOMETHING_ELSE_ON, FakeCaptionsServer, FakeVmix, device,
    surface, vmix_xml,
)

T0 = 1_760_000_000.0

# The Bolton hall's input, as its overlay-gate.json names it.
BOLTON = overlay_gate.Settings(device_name="Line In (Realtek(R) Audio)",
                               device_host_api="MME")


class Rig:
    def __init__(self, settings=None, **server):
        self.vmix = FakeVmix()
        self.server = FakeCaptionsServer(**server)
        self.lines: list[str] = []
        self.gate = overlay_gate.Gate(settings or overlay_gate.DEFAULTS,
                                      self.vmix, self.server, self.lines.append)
        self.now = T0

    def step(self, after: float = 1.0):
        self.now += after
        self.gate.step(self.now)

    def log(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def rig() -> Rig:
    return Rig()


class TestTheButtonStartsTheCaptions:
    def test_overlay_2_on_starts_a_capture_on_the_speech_settings(self, rig):
        rig.vmix.xml = ON

        rig.step()

        # No device and no language: out of the box the server's own default
        # input and the direction in its .env, not one hall's.
        assert rig.server.starts == [{
            "source": "device",
            "sarvam": {"model": "saaras:v4", "force_flush_sec": 4,
                       "min_coverage": 0.4, "lag_limit_sec": 20},
            "gate": {},
        }]

    def test_the_hall_s_device_is_sent_by_name(self):
        rig = Rig(BOLTON)
        rig.vmix.xml = ON

        rig.step()

        [start] = rig.server.starts
        # By name: the server finds today's number for it (6 -> 1 in katha
        # week).
        assert start["device_name"] == "Line In (Realtek(R) Audio)"
        assert start["device_host_api"] == "MME"
        assert "device" not in start

    def test_a_running_capture_is_left_alone(self):
        rig = Rig(running=True)
        rig.vmix.xml = ON

        for _ in range(5):
            rig.step()

        assert rig.server.starts == []
        assert rig.server.stops == 0

    def test_one_press_is_one_start(self, rig):
        rig.vmix.xml = ON

        for _ in range(5):
            rig.step()

        assert len(rig.server.starts) == 1

    def test_another_input_on_overlay_2_is_not_the_captions(self, rig):
        rig.vmix.xml = SOMETHING_ELSE_ON

        rig.step()

        assert rig.server.starts == []

    def test_the_input_is_found_by_its_key_when_it_has_been_renamed(self):
        settings = overlay_gate.Settings(input_key="5d3c7e2a-captions")
        rig = Rig(settings)
        rig.vmix.xml = vmix_xml("34", title="Captions (old name)")

        rig.step()

        assert len(rig.server.starts) == 1

    def test_a_start_by_name_lands_on_today_s_number_for_it(self):
        rig = Rig(BOLTON, devices=[device(6, BOLTON.device_name, "MME"),
                           device(1, "Stereo Mix (Realtek(R) Audio)", "MME")])
        rig.vmix.xml = ON

        rig.step()

        assert rig.server.capturing_from == "6"

    def test_a_missing_device_is_logged_once_and_retried(self):
        rig = Rig(BOLTON)
        rig.server.devices = [device(0, "Stereo Mix (Realtek(R) Audio)", "MME")]
        rig.vmix.xml = ON

        for _ in range(5):
            rig.step()

        assert not rig.server.running
        assert rig.log().count("No input device named 'Line In (Realtek(R) Audio)' "
                               "under MME") == 1
        assert "capture start:" not in rig.log()

        rig.server.devices = [device(3, BOLTON.device_name, "MME")]
        rig.step()

        assert rig.server.capturing_from == "3"
        assert rig.log().count("capture start:") == 1


class TestTheButtonOffStopsTheCaptions:
    def test_off_for_the_grace_stops_the_capture(self):
        rig = Rig(running=True)
        rig.vmix.xml = OFF

        rig.step()                 # the OFF stretch begins
        for _ in range(9):
            rig.step()             # 9 s off
        assert rig.server.stops == 0

        rig.step()                 # 10 s off
        assert rig.server.stops == 1

    def test_the_stop_is_sent_once(self):
        rig = Rig(running=True)
        rig.vmix.xml = OFF

        for _ in range(30):
            rig.step()

        assert rig.server.stops == 1

    def test_a_quick_flick_cancels_the_stop(self):
        rig = Rig(running=True)
        rig.vmix.xml = OFF
        for _ in range(6):
            rig.step()

        rig.vmix.xml = ON
        rig.step()
        rig.vmix.xml = OFF         # off again: a new stretch, a full grace
        for _ in range(9):
            rig.step()

        assert rig.server.stops == 0
        assert rig.server.starts == []
        assert "stop cancelled" in rig.log()

    def test_the_grace_comes_from_the_settings(self):
        rig = Rig(overlay_gate.Settings(stop_grace_sec=3), running=True)
        rig.vmix.xml = OFF

        for _ in range(4):
            rig.step()

        assert rig.server.stops == 1


class TestVmixUnreadableIsNotOff:
    def test_a_closed_vmix_never_stops_the_capture(self):
        rig = Rig(running=True)
        rig.vmix.xml = None

        for _ in range(60):
            rig.step()

        assert rig.server.starts == []
        assert rig.server.stops == 0

    def test_a_vmix_without_the_captions_input_never_stops_the_capture(self):
        # vMix is up but the preset has not loaded the input yet.
        rig = Rig(running=True)
        rig.vmix.xml = vmix_xml(None, captions_input=False)

        for _ in range(60):
            rig.step()

        assert rig.server.stops == 0

    def test_a_vmix_restart_cancels_a_pending_stop(self):
        rig = Rig(running=True)
        rig.vmix.xml = OFF
        for _ in range(8):
            rig.step()

        rig.vmix.xml = None        # vMix restarting
        rig.step()
        rig.vmix.xml = OFF         # back, still off: the grace starts again
        for _ in range(5):
            rig.step()

        assert rig.server.stops == 0
        assert "pending stop cancelled" in rig.log()

    def test_unreadable_is_logged_once_per_outage(self, rig):
        rig.vmix.xml = None
        for _ in range(5):
            rig.step()
        rig.vmix.xml = ON
        rig.step()

        assert rig.log().count("vMix unreadable") == 1
        assert rig.log().count("vMix readable again") == 1


class TestAStartWaitsForTheStop:
    def test_off_then_on_starts_only_once_the_capture_has_stopped(self):
        # 7 Oct 09:43, by hand: OverlayInput2Out -> stop -> wait until
        # capture.running is false -> start. The gate has to do the same.
        rig = Rig(running=True, stop_takes_reads=3)
        rig.vmix.xml = OFF
        for _ in range(11):
            rig.step()
        assert rig.server.stops == 1

        rig.vmix.xml = ON
        rig.step()                 # still tearing down
        rig.step()
        assert rig.server.starts == []

        rig.step()                 # reports running: false
        rig.step()

        assert len(rig.server.starts) == 1
        posts = [p for verb, p, _ in rig.server.requests if verb == "POST"]
        assert posts == ["/api/stop", "/api/start"]

    def test_a_start_refused_as_already_running_is_tried_again(self):
        # The status can say stopped a moment before the server lets go of
        # the last capture. A 409 must not leave nothing capturing.
        rig = Rig(refuse_starts=1)
        rig.vmix.xml = ON

        rig.step()
        rig.step()

        assert len(rig.server.starts) == 2
        assert rig.server.running


class TestTheServerNotAnswering:
    def test_nothing_is_sent_and_it_is_logged_once(self, rig):
        rig.server.answering = False
        rig.vmix.xml = ON

        for _ in range(5):
            rig.step()

        assert rig.server.requests == []
        assert rig.log().count("captions server not answering") == 1

        rig.server.answering = True
        rig.step()

        assert len(rig.server.starts) == 1
        assert "captions server answering again" in rig.log()


class TestTheOverlayComesBack:
    """11 Oct morning: vMix opened before the server, its browser input never
    loaded the overlay page and never tried again, and the hall screen was
    blank for about an hour. The gate puts the page back by itself."""

    def test_a_missing_overlay_surface_is_navigated_to_the_agreed_url(self, rig):
        rig.vmix.xml = ON

        rig.step()                 # nothing reporting: the grace begins
        for _ in range(14):
            rig.step()             # 14 s
        assert rig.vmix.navigates == []

        rig.step()                 # 15 s

        assert rig.vmix.navigates == [{
            "Function": "BrowserNavigate",
            "Input": CAPTIONS_KEY,
            "Value": "http://127.0.0.1:8765/?overlay=1&lines=2&fs=64&fw=600"
                     "&tbg=25&panel=1&ailabel=1",
        }]
        assert rig.log().count("BrowserNavigate") == 1

    def test_a_reporting_overlay_surface_is_never_navigated_away(self):
        rig = Rig(surfaces=[surface("overlay-k3x9qa")])
        rig.vmix.xml = ON

        for _ in range(120):
            rig.step()

        assert rig.vmix.navigates == []

    def test_an_operator_page_is_not_the_overlay(self):
        # The operator page reports too. It is open on the desk all katha, and
        # says nothing about what vMix's input is showing.
        rig = Rig(surfaces=[surface("operator-7fw2mz")])
        rig.vmix.xml = ON

        for _ in range(16):        # the grace, and its first step
            rig.step()

        assert len(rig.vmix.navigates) == 1

    def test_an_overlay_surface_gone_stale_is_missing(self):
        # vMix lost the page mid-katha: its last report is kept, marked stale.
        rig = Rig(surfaces=[surface("overlay-k3x9qa")])
        rig.vmix.xml = ON
        for _ in range(30):
            rig.step()

        rig.server.surfaces = [surface("overlay-k3x9qa", stale=True)]
        rig.step()                 # the grace begins
        for _ in range(14):
            rig.step()
        assert rig.vmix.navigates == []
        rig.step()                 # 15 s

        assert len(rig.vmix.navigates) == 1

    def test_no_second_navigate_within_the_grace(self, rig):
        # The page needs a moment to load and report. A navigate every second
        # would never let it.
        rig.vmix.xml = ON
        for _ in range(16):
            rig.step()
        assert len(rig.vmix.navigates) == 1

        for _ in range(14):
            rig.step()

        assert len(rig.vmix.navigates) == 1

    def test_overlay_2_off_is_never_navigated(self, rig):
        # Off, nobody is looking at the input: leave it alone.
        for xml in (OFF, SOMETHING_ELSE_ON):
            rig.vmix.xml = xml
            for _ in range(60):
                rig.step()

        assert rig.vmix.navigates == []

    def test_an_unreadable_vmix_is_never_navigated(self, rig):
        for xml in (None, vmix_xml(None, captions_input=False)):
            rig.vmix.xml = xml
            for _ in range(60):
                rig.step()

        assert rig.vmix.calls == []

    def test_a_vmix_restart_starts_the_grace_again(self, rig):
        # A vMix that has just come back is still loading its inputs' pages.
        rig.vmix.xml = ON
        for _ in range(10):
            rig.step()
        rig.vmix.xml = None
        rig.step()

        rig.vmix.xml = ON
        rig.step()                 # the grace begins again
        for _ in range(14):
            rig.step()
        assert rig.vmix.navigates == []
        rig.step()

        assert len(rig.vmix.navigates) == 1

    def test_a_server_that_is_down_is_never_a_reason_to_navigate(self, rig):
        # No page can load from a server that is down, and a page that was
        # showing reconnects by itself once it is back: it reports within 5 s.
        rig.vmix.xml = ON
        for _ in range(10):
            rig.step()
        rig.server.answering = False
        for _ in range(120):
            rig.step()
        assert rig.vmix.navigates == []

        rig.server.answering = True
        rig.step()                 # the grace begins again
        for _ in range(14):
            rig.step()
        assert rig.vmix.navigates == []
        rig.step()

        assert len(rig.vmix.navigates) == 1

    def test_a_surface_that_never_reports_is_navigated_less_often_up_to_a_minute(self, rig):
        # A wrong overlay URL, say, or a page vMix's Chromium cannot run.
        # Navigating every 15 s all katha changes nothing and floods the log.
        rig.vmix.xml = ON
        at = []
        for _ in range(400):
            before = len(rig.vmix.navigates)
            rig.step()
            if len(rig.vmix.navigates) > before:
                at.append(rig.now - T0)

        gaps = [b - a for a, b in zip(at, at[1:])]
        assert at[0] == 16                 # the grace after the first step
        assert gaps[:4] == [30, 60, 60, 60]
        assert set(gaps[4:]) == {60}

    def test_a_grace_of_zero_still_waits_between_navigates(self):
        # The overlay reports the moment it loads and every 5 s after. A hall
        # that sets no grace must not have vMix navigating every second.
        rig = Rig(overlay_gate.Settings(navigate_grace_sec=0))
        rig.vmix.xml = ON

        for _ in range(20):
            rig.step()

        assert len(rig.vmix.navigates) == 4

    def test_a_surface_that_reports_again_resets_the_wait(self, rig):
        rig.vmix.xml = ON
        for _ in range(60):
            rig.step()                     # navigated at 15 s and 45 s
        rig.server.surfaces = [surface("overlay-k3x9qa")]
        for _ in range(30):
            rig.step()
        assert len(rig.vmix.navigates) == 2
        assert "overlay surface reporting again" in rig.log()

        rig.server.surfaces = [surface("overlay-k3x9qa", stale=True)]
        for _ in range(16):
            rig.step()

        assert len(rig.vmix.navigates) == 3

    def test_each_navigate_is_logged_with_its_reason(self):
        rig = Rig(surfaces=[surface("operator-7fw2mz"),
                            surface("overlay-k3x9qa", stale=True)])
        rig.vmix.xml = ON

        for _ in range(16):
            rig.step()

        [line] = [l for l in rig.lines if "BrowserNavigate" in l]
        assert "no overlay surface has reported for 15s" in line
        assert "overlay-k3x9qa stale" in line
        assert "'Captions Overlay'" in line
        assert "&tbg=25&panel=1&ailabel=1" in line

    def test_a_navigate_vmix_refuses_is_logged_and_tried_again(self, rig):
        rig.vmix.refuse_calls = "HTTP Error 500: Input not found"
        rig.vmix.xml = ON

        for _ in range(16 + 30):
            rig.step()

        assert len(rig.vmix.navigates) == 2
        assert rig.log().count("could not navigate") == 1


class TestTheHallSettingsFile:
    """A new hall sets the gate up by a file, not by editing the code."""

    def load(self, tmp_path, text: str | None):
        path = tmp_path / "overlay-gate.json"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        lines: list[str] = []
        return overlay_gate.load_settings(path, lines.append), "\n".join(lines)

    def test_a_missing_file_means_the_defaults_and_the_log_says_so(self, tmp_path):
        settings, log = self.load(tmp_path, None)

        assert settings == overlay_gate.DEFAULTS
        assert "no settings file" in log
        assert "defaults" in log

    def test_a_partial_file_keeps_the_defaults_for_the_rest(self, tmp_path):
        settings, log = self.load(tmp_path, json.dumps({
            "input_title": "Sabha Captions",
            "overlay": 3,
            "device_name": "Mic In (USB Audio)",
        }))

        assert settings.input_title == "Sabha Captions"
        assert settings.overlay == 3
        assert settings.device_name == "Mic In (USB Audio)"
        assert settings.stop_grace_sec == 10.0
        assert settings.speech == {"model": "saaras:v4", "force_flush_sec": 4,
                                   "min_coverage": 0.4, "lag_limit_sec": 20}
        assert "defaults for" in log
        assert "stop_grace_sec" in log
        assert "input_title" not in log.split("defaults for", 1)[1]

    def test_a_partial_speech_block_keeps_the_other_speech_settings(self, tmp_path):
        settings, _ = self.load(tmp_path, json.dumps({"speech": {"model": "saaras:v3"}}))

        assert settings.speech == {"model": "saaras:v3", "force_flush_sec": 4,
                                   "min_coverage": 0.4, "lag_limit_sec": 20}

    def test_a_file_that_is_not_json_means_the_defaults_and_the_log_says_so(self, tmp_path):
        settings, log = self.load(tmp_path, '{"overlay": 3,')

        assert settings == overlay_gate.DEFAULTS
        assert "could not read" in log
        assert "defaults" in log

    def test_a_whole_number_written_with_a_point_is_still_that_number(self, tmp_path):
        # JSON has one kind of number. "overlay": 2.0 must still find
        # overlay 2, not an overlay called "2.0" that never comes on.
        settings, _ = self.load(tmp_path, json.dumps({"overlay": 2.0}))
        rig = Rig(settings)
        rig.vmix.xml = ON

        rig.step()

        assert settings.overlay == 2
        assert len(rig.server.starts) == 1

    def test_a_setting_of_the_wrong_kind_falls_back_to_its_default(self, tmp_path):
        settings, log = self.load(tmp_path, json.dumps({
            "overlay": "two", "stop_grace_sec": 15, "input_title": 7}))

        assert settings.overlay == 2
        assert settings.input_title == "Captions Overlay"
        assert settings.stop_grace_sec == 15
        assert "overlay" in log and "input_title" in log

    def test_an_unknown_setting_is_named_in_the_log(self, tmp_path):
        settings, log = self.load(tmp_path, json.dumps({"overlay_number": 3}))

        assert settings == overlay_gate.DEFAULTS
        assert "overlay_number" in log

    def test_the_settings_reach_the_start(self, tmp_path):
        settings, _ = self.load(tmp_path, json.dumps({
            "device_name": "Mic In", "device_host_api": "",
            "speech": {"model": "saaras:v3"}}))
        rig = Rig(settings, devices=[device(4, "Mic In", "Windows WASAPI")])
        rig.vmix.xml = ON

        rig.step()

        [start] = rig.server.starts
        assert start["device_name"] == "Mic In"
        assert "device_host_api" not in start      # any host API
        assert start["sarvam"]["model"] == "saaras:v3"
        assert rig.server.capturing_from == "4"

    def test_the_navigate_carries_the_configured_overlay_url(self, tmp_path):
        url = "http://127.0.0.1:8765/?overlay=1&lines=3&fs=56&tbg=40&panel=1&ailabel=0"
        settings, _ = self.load(tmp_path, json.dumps({"overlay_url": url}))
        rig = Rig(settings)
        rig.vmix.xml = ON

        for _ in range(16):
            rig.step()

        assert [n["Value"] for n in rig.vmix.navigates] == [url]

    def test_a_comma_in_the_overlay_url_reaches_vmix_as_2c(self, tmp_path):
        # BrowserNavigate silently refuses a URL with a comma in it, and
        # `area` and `block` are written with commas. The page reads %2C back
        # as a comma.
        url = "http://127.0.0.1:8765/?overlay=1&area=0,800,1920,280&lines=2"
        settings, _ = self.load(tmp_path, json.dumps({"overlay_url": url}))
        rig = Rig(settings)
        rig.vmix.xml = ON

        for _ in range(16):
            rig.step()

        [value] = [n["Value"] for n in rig.vmix.navigates]
        assert value == "http://127.0.0.1:8765/?overlay=1&area=0%2C800%2C1920%2C280&lines=2"

    @pytest.mark.parametrize("url, why", [
        # `line` is no overlay setting: the page would show its defaults.
        ("http://127.0.0.1:8765/?overlay=1&line=2", "line"),
        # Without overlay=1 it is the operator page, which never reports as
        # the overlay: the gate would navigate to it for ever.
        ("http://127.0.0.1:8765/?lines=2&tbg=25", "overlay=1"),
        ("http://127.0.0.1:8765/?overlay=0&lines=2", "overlay=1"),
    ])
    def test_an_overlay_url_outside_the_frozen_vocabulary_falls_back_to_the_default(
            self, tmp_path, url, why):
        settings, log = self.load(tmp_path, json.dumps({"overlay_url": url}))

        assert settings.overlay_url == overlay_gate.DEFAULTS.overlay_url
        assert "overlay_url" in log and why in log

    def test_the_vocabulary_is_the_one_the_overlay_page_reads(self):
        # docs/adr/0002: the names in the URL are frozen. The gate checks a
        # hall's URL against them, so its list must be the page's own.
        web = overlay_gate.CHECKOUT / "web" / "src"
        source = "\n".join((web / f).read_text(encoding="utf-8")
                           for f in ("settings.ts", "OverlayView.tsx", "main.tsx"))
        read = set(re.findall(r'(?:qp|params\(\))\.(?:get|has)\("(\w+)"\)', source))

        assert read == overlay_gate.OVERLAY_URL_VOCABULARY

    def test_a_language_direction_set_in_the_file_reaches_the_start(self, tmp_path):
        settings, _ = self.load(tmp_path, json.dumps({
            "source_lang": "hi-IN", "target_lang": "en-IN"}))
        rig = Rig(settings)
        rig.vmix.xml = ON

        rig.step()

        [start] = rig.server.starts
        assert start["source_lang"] == "hi-IN"
        assert start["target_lang"] == "en-IN"

    def test_a_language_left_out_of_the_file_is_not_sent(self, tmp_path):
        # The server keeps its own: .env's DEFAULT_SOURCE_LANG, or the
        # direction the operator page last set.
        settings, _ = self.load(tmp_path, json.dumps({"target_lang": "gu-IN"}))
        rig = Rig(settings)
        rig.vmix.xml = ON

        rig.step()

        [start] = rig.server.starts
        assert "source_lang" not in start
        assert start["target_lang"] == "gu-IN"

    def test_the_example_file_lists_the_shipped_defaults(self):
        # The example is what a hall copies from. If it drifts from the code,
        # a hall that copies it runs settings nobody chose.
        example = json.loads((overlay_gate.CHECKOUT / "overlay-gate.example.json")
                             .read_text(encoding="utf-8"))

        assert overlay_gate.load_settings(
            overlay_gate.CHECKOUT / "overlay-gate.example.json", lambda _: None
        ) == overlay_gate.DEFAULTS
        assert set(example) == {f.name for f in dataclasses.fields(overlay_gate.Settings)}


class _Http:
    """Serve one fake over real HTTP on a free local port."""

    def __init__(self, fake, kind: str):
        import http.server
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.paths.append(self.path)
                if kind == "vmix":
                    if "?" in self.path:
                        # A function call: vMix answers it in plain text.
                        return self._send(200, b"Function completed successfully.",
                                          "text/plain")
                    xml = outer.script.pop(0) if len(outer.script) > 1 else outer.script[0]
                    fake.xml = xml
                    return self._send(200, fake.get(), "text/xml")
                self._send(200, json.dumps(fake.get(self.path)).encode(), "application/json")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                code, text = fake.post(self.path, json.loads(self.rfile.read(n) or b"{}"))
                self._send(code, text.encode(), "text/plain")

        self.script: list[bytes] = []
        self.paths: list[str] = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def hall(tmp_path):
    vmix, server = FakeVmix(), FakeCaptionsServer()
    vmix_http, server_http = _Http(vmix, "vmix"), _Http(server, "server")
    settings = tmp_path / "overlay-gate.json"
    settings.write_text(json.dumps({
        "vmix_api": vmix_http.url + "/api/",
        "captions_server": server_http.url,
        "stop_grace_sec": 0,
        "poll_sec": 0,
    }), encoding="utf-8")
    yield vmix_http, server, settings, tmp_path / "logs"
    vmix_http.close()
    server_http.close()


@pytest.fixture(autouse=True)
def _restore_gate_logger():
    log = logging.getLogger(overlay_gate.LOGGER)
    before = list(log.handlers)
    yield
    for h in log.handlers:
        if h not in before:
            h.close()
    log.handlers[:] = before


class TestTheEntryPoint:
    """Running the script behaves as the 7 Oct gate did."""

    def test_a_press_and_a_release_are_a_start_a_stop_and_a_line_each(self, hall):
        vmix, server, settings, log_dir = hall
        vmix.script = [ON, ON, OFF, OFF]

        overlay_gate.main(["--settings", str(settings), "--log-dir", str(log_dir)],
                          steps=4)

        assert len(server.starts) == 1
        assert server.stops == 1
        log = (log_dir / overlay_gate.LOG_FILENAME).read_text(encoding="utf-8")
        expected = ["overlay gate armed", "overlay 2 ON", "capture start",
                    "overlay 2 OFF", "capture stop"]
        at = [log.find(phrase) for phrase in expected]
        assert -1 not in at and at == sorted(at), log
        # Each line starts with its own time, as the 7 Oct log did.
        for line in log.splitlines():
            assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", line), line

    def test_the_navigate_reaches_vmix_as_its_api_function(self, hall):
        vmix, _, settings, log_dir = hall
        data = json.loads(settings.read_text())
        data["navigate_grace_sec"] = 0
        settings.write_text(json.dumps(data))
        vmix.script = [ON]

        overlay_gate.main(["--settings", str(settings), "--log-dir", str(log_dir)],
                          steps=1)

        [call] = [p for p in vmix.paths if "?" in p]
        path, query = call.split("?", 1)
        assert path == "/api/"
        assert urllib.parse.parse_qs(query) == {
            "Function": ["BrowserNavigate"], "Input": [CAPTIONS_KEY],
            "Value": [overlay_gate.DEFAULTS.overlay_url]}
        log = (log_dir / overlay_gate.LOG_FILENAME).read_text(encoding="utf-8")
        assert "Function completed successfully." in log

    def test_with_no_device_name_the_log_says_the_default_input_is_used(self, hall):
        # A new hall that has not set its input yet captures from whatever
        # the server's default is. The log has to say so before katha day.
        vmix, _, settings, log_dir = hall
        vmix.script = [OFF]

        overlay_gate.main(["--settings", str(settings), "--log-dir", str(log_dir)],
                          steps=1)

        log = (log_dir / overlay_gate.LOG_FILENAME).read_text(encoding="utf-8")
        assert "no device_name set: using the server's default input" in log

    def test_a_device_name_set_is_named_in_the_log(self, hall):
        vmix, _, settings, log_dir = hall
        data = json.loads(settings.read_text())
        data["device_name"] = "Line In (Realtek(R) Audio)"
        settings.write_text(json.dumps(data))
        vmix.script = [OFF]

        overlay_gate.main(["--settings", str(settings), "--log-dir", str(log_dir)],
                          steps=1)

        log = (log_dir / overlay_gate.LOG_FILENAME).read_text(encoding="utf-8")
        assert "no device_name set" not in log
        assert "device 'Line In (Realtek(R) Audio)' (any host API)" in log

    def test_vmix_closed_is_one_line_and_no_request(self, hall, tmp_path):
        _, server, settings, log_dir = hall
        data = json.loads(settings.read_text())
        data["vmix_api"] = "http://127.0.0.1:9/api/"   # nothing listens there
        settings.write_text(json.dumps(data))

        overlay_gate.main(["--settings", str(settings), "--log-dir", str(log_dir)],
                          steps=3)

        log = (log_dir / overlay_gate.LOG_FILENAME).read_text(encoding="utf-8")
        assert log.count("vMix unreadable") == 1
        assert server.requests == []

    def test_the_log_rolls_over_daily_and_keeps_two_weeks(self, tmp_path):
        overlay_gate.configure_log(tmp_path)

        [handler] = [h for h in logging.getLogger(overlay_gate.LOGGER).handlers
                     if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
        assert handler.when == "MIDNIGHT"
        assert handler.backupCount == 14

    def test_with_no_console_the_log_still_reaches_the_file(self, tmp_path, monkeypatch):
        # The logon task runs pythonw, where stdout is None.
        monkeypatch.setattr(sys, "stdout", None)

        path = overlay_gate.configure_log(tmp_path)
        logging.getLogger(overlay_gate.LOGGER).info("overlay 2 ON")

        assert "overlay 2 ON" in path.read_text(encoding="utf-8")
