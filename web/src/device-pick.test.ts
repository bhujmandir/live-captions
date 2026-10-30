import { describe, it, expect } from "vitest";
import { pickDevice, rememberDevice, readRememberedDevice, startDevice } from "./device-pick";
import type { AudioDevice } from "./types";

// Katha week: the page remembered the hall's input by its number, the
// number moved from 6 to 1 between boots, and a start landed on a silent
// "Stereo Mix". The page now remembers the name and looks for it again in
// the list the server gives today.

const LINE_IN = "Line In (Realtek(R) Audio)";

function dev(id: number, deviceName: string, hostApi: string,
             extra: Partial<AudioDevice> = {}): AudioDevice {
  return { id, name: `${deviceName} — ${hostApi}`, deviceName, hostApi,
           channels: 2, default: false, worksAt16k: true, ...extra };
}

// The week the hall's Line In was number 6 ...
const BEFORE = [
  dev(0, "Microsoft Sound Mapper - Input", "MME"),
  dev(1, "Stereo Mix (Realtek(R) Audio)", "MME"),
  dev(6, LINE_IN, "MME", { default: true }),
  dev(9, LINE_IN, "Windows WASAPI", { worksAt16k: false }),
];
// ... and after a reboot, when it was number 1.
const AFTER = [
  dev(0, "Microsoft Sound Mapper - Input", "MME"),
  dev(1, LINE_IN, "MME", { default: true }),
  dev(5, "Stereo Mix (Realtek(R) Audio)", "MME"),
  dev(6, "Webcam 4 (NDI Webcam Audio)", "MME"),
  dev(9, LINE_IN, "Windows WASAPI", { worksAt16k: false }),
];

describe("the device the page preselects", () => {
  it("is the remembered device found by name after the numbers moved", () => {
    const remembered = readRememberedDevice(rememberDevice(BEFORE[2]));

    expect(pickDevice(AFTER, remembered)).toEqual({ id: "1", missing: null });
  });

  it("keeps the remembered host API, not just the name", () => {
    const list = [
      dev(2, LINE_IN, "Windows WASAPI"),
      dev(3, LINE_IN, "MME"),
    ];

    expect(pickDevice(list, { name: LINE_IN, hostApi: "MME" }).id).toBe("3");
  });

  it("takes any host API when none was remembered, as the server does", () => {
    expect(pickDevice(AFTER, { name: LINE_IN, hostApi: "" }).id).toBe("1");
  });

  it("is nothing, with a notice, when the remembered name is gone", () => {
    const remembered = { name: LINE_IN, hostApi: "MME" };
    const list = AFTER.filter((d) => d.deviceName !== LINE_IN);

    expect(pickDevice(list, remembered)).toEqual({ id: "", missing: remembered });
  });

  it("is nothing when only the same name under another host API is left", () => {
    const remembered = { name: LINE_IN, hostApi: "MME" };
    const list = [dev(9, LINE_IN, "Windows WASAPI")];

    expect(pickDevice(list, remembered)).toEqual({ id: "", missing: remembered });
  });

  it("is the system default when nothing is remembered", () => {
    expect(pickDevice(AFTER, null)).toEqual({ id: "1", missing: null });
  });

  it("is the first device when nothing is remembered and none is the default", () => {
    const list = AFTER.map((d) => ({ ...d, default: false }));

    expect(pickDevice(list, null)).toEqual({ id: "0", missing: null });
  });

  it("is the device a capture is reading from, whatever was remembered", () => {
    // A capture started by the overlay gate, seen by a page opened mid-katha.
    expect(pickDevice(AFTER, { name: "Mic In", hostApi: "MME" }, "1"))
      .toEqual({ id: "1", missing: null });
  });
});

describe("what the page remembers", () => {
  it("survives being stored and read back", () => {
    expect(readRememberedDevice(rememberDevice(AFTER[1])))
      .toEqual({ name: LINE_IN, hostApi: "MME" });
  });

  it("is nothing for an empty store or a number stored by an older page", () => {
    // The older page stored "6" under another key. A bare number is the
    // thing that moved, so it is not read as a remembered device.
    expect(readRememberedDevice(null)).toBeNull();
    expect(readRememberedDevice("6")).toBeNull();
    expect(readRememberedDevice("{not json")).toBeNull();
  });
});

describe("the device a start asks for", () => {
  // The server re-reads its device list before a start by name, so the
  // number the page chose can belong to another device by the time Start is
  // pressed. The name is what the server goes by; the number only tells two
  // inputs of the same name apart.
  it("is the chosen device's name and host API, with its number", () => {
    expect(startDevice(AFTER, "1")).toEqual({
      device: "1", device_name: LINE_IN, device_host_api: "MME",
    });
  });

  it("tells two inputs of one name apart by the number", () => {
    const twins = [dev(2, "USB Audio Device", "MME"), dev(4, "USB Audio Device", "MME")];

    expect(startDevice(twins, "4")).toEqual({
      device: "4", device_name: "USB Audio Device", device_host_api: "MME",
    });
  });

  it("is the number alone when the list does not have it", () => {
    expect(startDevice(AFTER, "7")).toEqual({ device: "7" });
  });
});
