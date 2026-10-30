"""A start lands on the hall's input by its name, whatever number it has today.

Windows renumbers the input devices between boots. In katha week the hall's
`Line In` moved from 6 to 1, the operator page started on the number it had
remembered, and the capture landed on a silent "Stereo Mix". So the
server answers a start by name: it finds the device of that name in today's
list and captures from it, or refuses. It never falls back to another device.

PortAudio reads Windows' device list once, when it initialises, so an input
plugged in, disabled or re-enabled after the server started is listed as it
was. A start by name re-reads the list first, unless a stream is open or a
capture is under way (a re-read under an open stream pulls the device out
from under it), and not more than once every ten seconds (the overlay gate
asks every second).

These tests drive the real handlers over HTTP, the way the operator page and
the overlay gate reach them. The one fake is `sounddevice`, the boundary to
the audio hardware: it lists the mandir PC's devices as the PC reported them.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import live_captions

from .fakes import FakeBroadcaster

# The handlers read plain string keys off the app, as the server sets them.
pytestmark = pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey")

HOST_APIS = [{"name": "MME"}, {"name": "Windows DirectSound"},
             {"name": "Windows WASAPI"}]
MME, DSOUND, WASAPI = 0, 1, 2


def device(name, hostapi, inputs=2, works=True):
    return {"name": name, "hostapi": hostapi, "max_input_channels": inputs,
            "_works": works}


# The mandir PC, 24 Oct: Line In is index 1 under MME and is the
# Windows default input; WASAPI lists the same Line In again and refuses 16 kHz.
MANDIR = [
    device("Microsoft Sound Mapper - Input", MME),
    device("Line In (Realtek(R) Audio)", MME),
    device("Microsoft Sound Mapper - Output", MME, inputs=0),
    device("Speakers (Realtek(R) Audio)", MME, inputs=0),
    device("Primary Sound Capture Driver", DSOUND),
    device("Stereo Mix (Realtek(R) Audio)", MME),
    device("Webcam 4 (NDI Webcam Audio)", MME),
    device("Line In (Realtek(R) Audio)", DSOUND),
    device("Speakers (Realtek(R) Audio)", WASAPI, inputs=0),
    device("Line In (Realtek(R) Audio)", WASAPI, works=False),
]

# The same PC on the week the number moved: Line In under MME is index 6.
RENUMBERED = [
    device("Microsoft Sound Mapper - Input", MME),
    device("Stereo Mix (Realtek(R) Audio)", MME),
    device("Microsoft Sound Mapper - Output", MME, inputs=0),
    device("Speakers (Realtek(R) Audio)", MME, inputs=0),
    device("Primary Sound Capture Driver", DSOUND),
    device("Webcam 4 (NDI Webcam Audio)", MME),
    device("Line In (Realtek(R) Audio)", MME),
    device("Line In (Realtek(R) Audio)", WASAPI, works=False),
]


class FakeSounddevice(types.ModuleType):
    """`sounddevice` over a PortAudio that reads the device table when it
    initialises and not again, as the real one does. `plug` changes what
    Windows has; the server sees it only after `_terminate` + `_initialize`.
    """

    def __init__(self, devices, default_input):
        super().__init__("sounddevice")
        self.table = devices      # what PortAudio read when it initialised
        self.windows = devices    # what Windows has now
        self.default = types.SimpleNamespace(device=[default_input, 3])
        self._initialized = 1
        self.rereads = 0
        self.fail_initialize = 0  # how many initialises fail before one works
        self.open_streams = 0
        self.opened_on: list = []  # the `device` of every stream opened
        self.terminated_under_a_stream = False
        fake = self

        class InputStream:
            def __init__(self, **kw):
                fake.opened_on.append(kw.get("device"))

            def __enter__(self):
                fake.open_streams += 1
                return self

            def __exit__(self, *_):
                fake.open_streams -= 1

        self.InputStream = InputStream

    def plug(self, devices):
        self.windows = devices

    def query_hostapis(self):
        return HOST_APIS

    def query_devices(self):
        if not self._initialized:
            raise RuntimeError("PortAudio not initialized [PaErrorCode -10000]")
        return [{k: v for k, v in d.items() if k != "_works"} for d in self.table]

    def check_input_settings(self, device, **_):
        if not self.table[device]["_works"]:
            raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")

    def _terminate(self):
        self.terminated_under_a_stream |= self.open_streams > 0
        self._initialized -= 1

    def _initialize(self):
        if self.fail_initialize:
            self.fail_initialize -= 1
            raise RuntimeError("Error initializing PortAudio [PaErrorCode -9999]")
        self._initialized += 1
        self.table = self.windows
        self.rereads += 1


class Server:
    """The real handlers on a real HTTP port, with the capture itself held:
    a start reaches the point of capturing and waits there until a stop."""

    def __init__(self, client: TestClient, broadcaster: FakeBroadcaster):
        self.client = client
        self.broadcaster = broadcaster

    async def devices(self) -> list[dict]:
        r = await self.client.get("/api/devices")
        assert r.status == 200
        return (await r.json())["devices"]

    async def start(self, **body):
        return await self.client.post("/api/start", json={
            "source": "device", "source_lang": "gu-IN", "target_lang": "en-IN",
            **body})

    async def stop(self):
        return await self.client.post("/api/stop", json={})

    async def monitor(self, device: str):
        return await self.client.post("/api/monitor/start", json={"device": device})

    async def capture(self) -> dict:
        r = await self.client.get("/api/overlay-status")
        assert r.status == 200
        return (await r.json())["capture"]


@pytest.fixture
def hall(monkeypatch):
    def install(devices, default_input=1) -> FakeSounddevice:
        sd = FakeSounddevice(devices, default_input)
        monkeypatch.setitem(sys.modules, "sounddevice", sd)
        return sd
    install(MANDIR)
    return install


@pytest.fixture
async def server(monkeypatch, hall):
    async def held_capture(audio_gen, broadcaster, use_pp, stop_event, state,
                           **_):
        # Opens the device's stream, as a capture does, and holds it open.
        pump = asyncio.ensure_future(audio_gen.__anext__())
        state["capture_running"] = True
        try:
            await stop_event.wait()
        finally:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            await audio_gen.aclose()

    monkeypatch.setattr(live_captions, "sarvam_loop", held_capture)
    broadcaster = FakeBroadcaster()
    app = web.Application()
    app["broadcaster"] = broadcaster
    app["use_pp"] = False
    app["state"] = {"caption_task": None, "stop_event": None,
                    "monitor_task": None, "monitor_stop_event": None}
    app.router.add_get("/api/devices", live_captions.handle_devices)
    app.router.add_post("/api/start", live_captions.handle_start)
    app.router.add_post("/api/direction", live_captions.handle_direction)
    app.router.add_post("/api/stop", live_captions.handle_stop)
    app.router.add_post("/api/monitor/start", live_captions.handle_monitor_start)
    app.router.add_post("/api/monitor/stop", live_captions.handle_monitor_stop)
    app.router.add_get("/api/overlay-status", live_captions.handle_overlay_status)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield Server(client, broadcaster)
    finally:
        await client.post("/api/stop", json={})
        await client.post("/api/monitor/stop", json={})
        for _ in range(10):
            await asyncio.sleep(0)
        await client.close()


class TestTheDeviceList:
    async def test_the_system_default_input_is_marked(self, server):
        devices = await server.devices()

        assert [d["id"] for d in devices if d["default"]] == ["1"]


LINE_IN = "Line In (Realtek(R) Audio)"


class TestAStartByName:
    @pytest.mark.parametrize("devices, today", [(MANDIR, "1"), (RENUMBERED, "6")])
    async def test_it_captures_from_the_device_of_that_name_today(
            self, server, hall, devices, today):
        hall(devices)

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == today

    async def test_an_unknown_name_is_refused_and_nothing_captures(self, server):
        r = await server.start(device_name="Mic In (USB Audio)", device_host_api="MME")

        assert r.status == 400
        assert "Mic In (USB Audio)" in await r.text()
        capture = await server.capture()
        assert capture["running"] is False
        assert capture["device"] is None

    async def test_the_name_under_another_host_api_is_refused(self, server, hall):
        # Not a fall-back to the Line In under MME: the start asked for WDM-KS.
        r = await server.start(device_name=LINE_IN,
                               device_host_api="Windows WDM-KS")

        assert r.status == 400
        assert (await server.capture())["running"] is False

    async def test_the_name_alone_skips_an_entry_that_cannot_capture_at_16k(
            self, server, hall):
        hall([device("Speakers (Realtek(R) Audio)", WASAPI, inputs=0),
              device(LINE_IN, WASAPI, works=False),
              device(LINE_IN, MME)])

        r = await server.start(device_name=LINE_IN)

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "2"

    async def test_the_name_wins_over_a_stale_index_sent_with_it(self, server, hall):
        hall(RENUMBERED)

        r = await server.start(device="1", device_name=LINE_IN,
                               device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "6"


class TestTwoDevicesOfTheSameName:
    """Two identical USB inputs list under one name. The operator page sends
    the number of the one chosen along with the name, to tell them apart."""

    TWINS = [
        device("Microsoft Sound Mapper - Input", MME),
        device("Line In (Realtek(R) Audio)", MME),
        device("USB Audio Device", MME),
        device("Stereo Mix (Realtek(R) Audio)", MME),
        device("USB Audio Device", MME),
        device("USB Audio Device", WASAPI),
    ]

    async def test_the_index_sent_with_the_name_picks_between_them(self, server, hall):
        hall(self.TWINS)

        r = await server.start(device="4", device_name="USB Audio Device",
                               device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "4"

    async def test_with_no_index_the_lowest_is_taken(self, server, hall):
        hall(self.TWINS)

        r = await server.start(device_name="USB Audio Device", device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "2"

    async def test_an_index_of_the_name_under_another_host_api_is_not_taken(
            self, server, hall):
        hall(self.TWINS)

        r = await server.start(device="5", device_name="USB Audio Device",
                               device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "2"


class TestAStartByIndex:
    async def test_it_still_captures_from_that_index(self, server):
        r = await server.start(device="5")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "5"


class TestAStartWithNoDevice:
    async def test_it_captures_from_the_default_input(self, server, hall):
        # The overlay gate of a hall that has not named its input sends no
        # device at all. That is PortAudio's default input, not a refusal.
        sd = hall(MANDIR)

        r = await server.start()

        assert r.status == 200, await r.text()
        await until(lambda: sd.opened_on)
        assert sd.opened_on == [None]
        assert sd.rereads == 0     # nothing to look up by name

    async def test_a_start_with_no_language_keeps_the_hall_s_direction(self, server):
        # What the overlay gate sends when the hall's file sets no language:
        # the direction set on the server (.env, or the operator page) stands.
        r = await server.client.post("/api/direction",
                                     json={"source": "hi-IN", "target": "en-IN"})
        assert r.status == 200, await r.text()

        r = await server.client.post("/api/start", json={
            "source": "device", "sarvam": {"model": "saaras:v4"}, "gate": {}})

        assert r.status == 200, await r.text()
        started = await r.json()
        assert (started["source"], started["target"]) == ("hi-IN", "en-IN")


WITHOUT_LINE_IN = [d for d in MANDIR if d["name"] != LINE_IN]


def names(devices) -> list[str]:
    return [d["deviceName"] for d in devices]


async def until(condition, turns: int = 50):
    """Let the event loop run until `condition()` holds (a stream opening or
    closing happens a few turns after the request that caused it)."""
    for _ in range(turns):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the condition never held")


class TestADeviceChangedAfterTheServerStarted:
    async def test_a_device_plugged_in_since_is_found_by_its_name(self, server, hall):
        sd = hall(WITHOUT_LINE_IN)
        sd.plug(MANDIR)        # re-enabled in Windows after the server started

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "1"

    async def test_a_device_disabled_since_is_refused_not_opened_stale(self, server, hall):
        # Dry run E6: the input disabled with the server already running. The
        # list PortAudio read at the start still names it.
        sd = hall(MANDIR)
        sd.plug(WITHOUT_LINE_IN)

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 400
        assert LINE_IN in await r.text()
        assert (await server.capture())["running"] is False

    async def test_a_name_still_missing_after_the_re_read_is_refused(self, server, hall):
        hall(WITHOUT_LINE_IN)

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 400
        assert (await server.capture())["running"] is False

    async def test_the_device_list_shows_a_device_plugged_in_since(self, server, hall):
        sd = hall(WITHOUT_LINE_IN)
        sd.plug(MANDIR)

        assert LINE_IN in names(await server.devices())

    async def test_a_re_read_that_fails_once_is_tried_again_at_once(self, server, hall):
        sd = hall(WITHOUT_LINE_IN)
        sd.plug(MANDIR)
        sd.fail_initialize = 1

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 200, await r.text()
        assert (await server.capture())["device"] == "1"


class TestNoReReadUnderAnOpenStream:
    async def test_the_device_list_is_not_re_read_while_capturing(self, server, hall):
        sd = hall(MANDIR)
        assert (await server.start(device="5")).status == 200
        await until(lambda: sd.open_streams == 1)
        sd.plug(RENUMBERED)

        devices = await server.devices()

        assert sd.rereads == 0
        assert not sd.terminated_under_a_stream
        assert sd.open_streams == 1
        assert names(devices) == [d["name"] for d in MANDIR if d["max_input_channels"]]

    async def test_the_device_list_is_not_re_read_under_the_level_monitor(
            self, server, hall):
        sd = hall(MANDIR)
        assert (await server.monitor("1")).status == 200
        await until(lambda: sd.open_streams == 1)
        sd.plug(RENUMBERED)

        await server.devices()

        assert sd.rereads == 0
        assert not sd.terminated_under_a_stream

    async def test_it_is_re_read_again_once_the_capture_has_stopped(self, server, hall):
        sd = hall(WITHOUT_LINE_IN)
        assert (await server.start(device="5")).status == 200
        await until(lambda: sd.open_streams == 1)
        await server.stop()
        await until(lambda: sd.open_streams == 0)
        for _ in range(50):
            if not (await server.capture())["running"]:
                break
        assert not (await server.capture())["running"]
        sd.plug(MANDIR)

        assert LINE_IN in names(await server.devices())

    async def test_a_start_by_name_releases_the_level_monitor_before_the_re_read(
            self, server, hall):
        sd = hall(WITHOUT_LINE_IN)
        assert (await server.monitor("0")).status == 200
        await until(lambda: sd.open_streams == 1)
        sd.plug(MANDIR)

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 200, await r.text()
        assert sd.rereads == 1
        assert not sd.terminated_under_a_stream


class TestTheReReadIsRateLimited:
    async def test_at_most_one_re_read_every_ten_seconds(self, server, hall, clock):
        sd = hall(WITHOUT_LINE_IN)

        for _ in range(5):        # the gate, once a second
            await server.start(device_name=LINE_IN, device_host_api="MME")
            clock.advance(1)
        assert sd.rereads == 1

        clock.advance(5)          # ten seconds after the first
        sd.plug(MANDIR)
        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 200, await r.text()
        assert sd.rereads == 2

    async def test_the_device_list_shares_the_limit(self, server, hall, clock):
        sd = hall(WITHOUT_LINE_IN)
        await server.devices()
        clock.advance(3)
        sd.plug(MANDIR)

        r = await server.start(device_name=LINE_IN, device_host_api="MME")

        assert r.status == 400
        assert sd.rereads == 1
