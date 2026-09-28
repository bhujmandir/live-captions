import type { LinkState, LinkReason, Connection } from "./types";

// Whether a surface should say out loud that it has lost the server.
//
// Issue #57 — see docs/how-it-works.md, "When the server is not there at all", for the
// full account. In short: `main.tsx` composites a "server unreachable" panel
// but deliberately suppresses it for `?overlay=1`, and the hall was left with
// nothing to say for 98 minutes.
//
// Both decisions live here, as pure functions, for one reason: the surface
// that got this wrong renders inside vMix's Chromium 51, where none of this
// can be run or inspected. Anything that cannot be asserted off the browser
// cannot be trusted before a katha.

export type { Connection };

/**
 * How long the socket must stay shut before the hall is told.
 *
 * `api.ts` retries every 1500ms, so a normal blip is down for a second or
 * two; anything shorter than a few of those retries would strobe a red
 * pill on the LED wall in front of the sabha. Ten seconds of nothing is
 * no longer a blip — it is the beginning of outage 1.
 */
export const HALL_OFFLINE_AFTER_MS = 10_000;

/**
 * The same window for the operator's page, and much shorter.
 *
 * The sabha must not be shown a fault that resolves itself; the operator
 * must. But it still has to be longer than one retry cycle, because the
 * socket passes back through `connecting` on every attempt — key the desk
 * banner off that state directly and it blinks off and on at 1.5s for the
 * whole outage, which is a worse lie than saying nothing.
 */
export const DESK_OFFLINE_AFTER_MS = 2_500;

/**
 * This browser's socket, and how long it has been in that state.
 *
 * `downSince` is when the socket stopped being open — including a page that
 * has never connected at all, which is what outage 1 looked like from vMix:
 * the overlay loads at machine start and the server never arrives.
 * `connecting` forever is the same fault as a socket that dropped, and the
 * grace window is what keeps the normal boot race quiet.
 */
export type SocketState = {
  conn: Connection;
  downSince: number | null;
  now: number;
};

function socketLost({ conn, downSince, now }: SocketState, graceMs: number): boolean {
  if (conn === "open") return false;
  if (downSince === null) return false;
  return now - downSince >= graceMs;
}

export type HallNotice = "none" | "offline";

/** What the hall screen should show about the connection. */
export function hallNotice(socket: SocketState): HallNotice {
  return socketLost(socket, HALL_OFFLINE_AFTER_MS) ? "offline" : "none";
}

/** The server's link to the speech service, and whether it should be up. */
export type SpeechLink = {
  state: LinkState;
  reason: LinkReason;
  running: boolean;
};

export type DeskNotice = "none" | "reconnecting" | "down";

/**
 * What the operator's page should show about the connection.
 *
 * Two different links are being judged, and the order matters:
 *
 *   `socket` — this page's socket to the captions server. If it is shut,
 *              nothing else on this page is knowable, INCLUDING `running`.
 *              The old banner was gated behind `running`, so a desk that
 *              had not pressed Start, or that reloaded mid-outage, was
 *              shown nothing at all while the server was gone.
 *
 *   `link`   — the server's link to the speech service. Only meaningful
 *              once we can hear the server, and only a fault while a
 *              session is actually capturing.
 */
export function deskNotice(socket: SocketState, link: SpeechLink): DeskNotice {
  if (socketLost(socket, DESK_OFFLINE_AFTER_MS)) return "down";
  // Still inside the grace window, or a page that has only just opened. A
  // page must never open with a fault painted on it.
  if (socket.conn !== "open") return "none";

  if (!link.running) return "none";
  if (link.state !== "disconnected") return "none";
  // A deliberate close — a direction flip, or the service hanging up an
  // idle connection — recovers on its own in about a second.
  return link.reason === "closed" ? "reconnecting" : "down";
}
