// Which input device the operator page preselects.
//
// The page remembers the device by its NAME and host API, never its number:
// Windows renumbers the inputs between boots, and in katha week a remembered
// number (6, which had become 1) put a start on a silent "Stereo Mix".
// When the remembered name is not in today's list the page preselects
// nothing and says so, rather than guessing at another device.

import type { AudioDevice } from "./types";

export type RememberedDevice = { name: string; hostApi: string };

export type DevicePick = {
  id: string;                         // "" = nothing preselected
  missing: RememberedDevice | null;   // the remembered device, when it is gone
};

export function pickDevice(
  list: AudioDevice[],
  remembered: RememberedDevice | null,
  capturingFrom?: string,
): DevicePick {
  // A capture already reading from a device (started here, elsewhere or by
  // the overlay gate): show that one.
  if (capturingFrom) return { id: String(capturingFrom), missing: null };

  if (remembered) {
    // An empty host API is any host API, as the server reads it.
    const same = list.filter((d) => d.deviceName === remembered.name
      && (!remembered.hostApi || d.hostApi === remembered.hostApi));
    // As the server does for a start by name: a usable entry first.
    const found = same.filter((d) => d.worksAt16k !== false)[0] || same[0];
    return found ? { id: String(found.id), missing: null }
                 : { id: "", missing: remembered };
  }

  const fallback = list.filter((d) => d.default)[0] || list[0];
  return { id: fallback ? String(fallback.id) : "", missing: null };
}

// localStorage text for a device the operator chose.
export function rememberDevice(d: AudioDevice): string {
  return JSON.stringify({ name: d.deviceName || d.name, hostApi: d.hostApi || "" });
}

// The remembered device, or null for nothing stored or anything unreadable.
export function readRememberedDevice(raw: string | null): RememberedDevice | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    if (v && typeof v === "object" && typeof v.name === "string" && v.name) {
      return { name: v.name, hostApi: typeof v.hostApi === "string" ? v.hostApi : "" };
    }
  } catch { /* fall through */ }
  return null;
}

// The device fields of a start. The server goes by the name (and host API),
// looked up in its own list at that moment; the number only chooses between
// inputs of the same name, and counts for nothing if it is another device.
export type StartDevice = { device: string; device_name?: string; device_host_api?: string };

export function startDevice(list: AudioDevice[], selectedId: string): StartDevice {
  const d = list.filter((x) => String(x.id) === selectedId)[0];
  if (!d || !d.deviceName) return { device: selectedId };
  const out: StartDevice = { device: selectedId, device_name: d.deviceName };
  if (d.hostApi) out.device_host_api = d.hostApi;
  return out;
}
