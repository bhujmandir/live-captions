import { describe, it, expect } from "vitest";
import {
  hallNotice, deskNotice, HALL_OFFLINE_AFTER_MS, DESK_OFFLINE_AFTER_MS,
  type Connection,
} from "./link-health";
import { VirtualClock } from "./testing";

// Issue #57, outage 1: the server was not running for 98 minutes and the
// hall screen looked exactly the way it looks when nobody is speaking —
// empty. Nobody in the sabha, and nobody at the desk, could tell a quiet
// katha from a dead one.
//
// These are the two decisions that failed. Both are pure so they can be
// asserted without a browser, which matters: the surface that got this
// wrong renders in vMix's Chromium 51, where nothing here can be run.

describe("the hall screen has to admit it is disconnected", () => {
  const clock = () => new VirtualClock();

  it("says nothing while the socket is open", () => {
    const c = clock();
    expect(hallNotice({ conn: "open", downSince: null, now: c.now() })).toBe("none");
  });

  it("says nothing on a fresh page that has not connected yet", () => {
    // A page opening 200ms before the server finishes booting must not
    // paint a fault on the LED wall. This is the normal daily sequence.
    const c = clock();
    const openedAt = c.now();
    expect(hallNotice({ conn: "connecting", downSince: openedAt, now: c.advance(200) })).toBe("none");
  });

  it("rides out a blip that reconnects inside the grace window", () => {
    const c = clock();
    const wentDown = c.now();
    c.advance(HALL_OFFLINE_AFTER_MS - 1);
    expect(hallNotice({ conn: "closed", downSince: wentDown, now: c.now() })).toBe("none");
  });

  it("admits it once the socket has stayed down past the grace window", () => {
    const c = clock();
    const wentDown = c.now();
    c.advance(HALL_OFFLINE_AFTER_MS);
    expect(hallNotice({ conn: "closed", downSince: wentDown, now: c.now() })).toBe("offline");
  });

  it("admits it for a server that never answered at all", () => {
    // vMix loads the overlay at machine start; the server may be minutes
    // behind it, or in outage 1's case never arrive. `connecting` for
    // that long is the same fault as a socket that dropped.
    const c = clock();
    const openedAt = c.now();
    c.advance(HALL_OFFLINE_AFTER_MS + 1);
    expect(hallNotice({ conn: "connecting", downSince: openedAt, now: c.now() })).toBe("offline");
  });

  it("goes quiet again the moment the socket is back", () => {
    const c = clock();
    const wentDown = c.now();
    c.advance(HALL_OFFLINE_AFTER_MS * 10);
    expect(hallNotice({ conn: "closed", downSince: wentDown, now: c.now() })).toBe("offline");
    expect(hallNotice({ conn: "open", downSince: null, now: c.now() })).toBe("none");
  });

  it("stays quiet if nothing has recorded when the link went down", () => {
    expect(hallNotice({ conn: "closed", downSince: null, now: 1_000_000 })).toBe("none");
  });

  it("waits long enough that a reconnect cycle cannot flash it", () => {
    // api.ts retries every 1500ms. A notice shorter than a couple of
    // those would strobe on the hall screen during a normal blip.
    expect(HALL_OFFLINE_AFTER_MS).toBeGreaterThanOrEqual(5000);
  });
});

describe("the desk has to be told even when it does not know what is running", () => {
  // The desk's socket, expressed the way the tests read best: how long it
  // has been in this state, not when it entered it.
  const socket = (conn: Connection, downForMs: number | null) => ({
    conn,
    downSince: downForMs === null ? null : 1_000_000 - downForMs,
    now: 1_000_000,
  });
  const DOWN_LONG_ENOUGH = DESK_OFFLINE_AFTER_MS;
  const idle = { state: "idle", reason: null, running: false } as const;

  it("shows the fault when this page has lost the server, running or not", () => {
    // The bug: the banner was gated behind `running`, and `running` is
    // exactly the thing a page with no socket cannot know. A desk that
    // had not pressed Start — or that reloaded during the outage — was
    // shown nothing at all.
    const shut = socket("closed", DOWN_LONG_ENOUGH);
    expect(deskNotice(shut, { state: "disconnected", reason: null, running: false })).toBe("down");
    expect(deskNotice(shut, { state: "disconnected", reason: null, running: true })).toBe("down");
  });

  it("does not open a fresh page with a fault on it", () => {
    expect(deskNotice(socket("connecting", 0), idle)).toBe("none");
  });

  it("does not blink off every time the socket retries", () => {
    // The regression this window exists for. `api.ts` reconnects every
    // 1500ms and passes back through `connecting` on each attempt, so a
    // banner keyed straight off `closed` strobed at 1.5s for the whole
    // outage — worse than saying nothing, because the operator learns to
    // read the flicker as recovery.
    const downFor = DESK_OFFLINE_AFTER_MS * 4;
    expect(deskNotice(socket("closed", downFor), idle)).toBe("down");
    expect(deskNotice(socket("connecting", downFor), idle)).toBe("down");
  });

  it("rides out a blip shorter than the window", () => {
    expect(deskNotice(socket("closed", DESK_OFFLINE_AFTER_MS - 1), idle)).toBe("none");
  });

  it("tells the operator sooner than it tells the hall", () => {
    // The sabha must not be shown a fault that resolves itself. The
    // operator must — they are the one who can do something about it.
    expect(DESK_OFFLINE_AFTER_MS).toBeLessThan(HALL_OFFLINE_AFTER_MS);
    expect(DESK_OFFLINE_AFTER_MS).toBeGreaterThan(1500);
  });

  it("stays calm when the server is there and nothing is capturing", () => {
    expect(deskNotice(socket("open", null), idle)).toBe("none");
  });

  it("stays calm when the link is fine mid-katha", () => {
    expect(deskNotice(socket("open", null),
      { state: "connected", reason: null, running: true })).toBe("none");
  });

  it("calls a deliberate close a reconnect, not a fault", () => {
    // A direction flip, or the service hanging up an idle connection.
    // Painting that fault-red teaches the operator to ignore the one
    // colour that matters.
    expect(deskNotice(socket("open", null),
      { state: "disconnected", reason: "closed", running: true })).toBe("reconnecting");
  });

  it("calls a dropped or errored link a fault", () => {
    expect(deskNotice(socket("open", null),
      { state: "disconnected", reason: "dropped", running: true })).toBe("down");
    expect(deskNotice(socket("open", null),
      { state: "disconnected", reason: "error", running: true })).toBe("down");
  });

  it("says nothing about the speech link when nothing is capturing", () => {
    // The server is reachable; it simply is not running a session. That
    // is not a fault and must not be painted as one.
    expect(deskNotice(socket("open", null),
      { state: "disconnected", reason: "dropped", running: false })).toBe("none");
  });
});
