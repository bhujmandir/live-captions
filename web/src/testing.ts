// Test doubles for the overlay suite.
//
// Deliberately mirrors `tests/fakes.py` on the Python side, where `VirtualClock`
// is advanced one step at a time so a five-minute silence costs a millisecond.
// The overlay's most important behaviours are all measured in seconds of NOT
// happening — a caption staying up, a caption coming down — and a suite that
// waits those seconds out is a suite nobody runs.

import { create } from "zustand";
import { captionStore } from "./store";
import { handleWsMessage } from "./api";
import type { WsMsg } from "./types";

/** A clock the test moves by hand. Never reads the real time. */
export class VirtualClock {
  private t: number;

  constructor(startMs = 1_000_000) {
    this.t = startMs;
  }

  /** Current virtual time in epoch ms. */
  now(): number {
    return this.t;
  }

  /** Move time forward. Returns the new now, so calls can be chained inline. */
  advance(ms: number): number {
    this.t += ms;
    return this.t;
  }
}

/**
 * A store isolated to one test, plus the wire.
 *
 * `send` feeds a message through the REAL `/ws` handler rather than calling
 * store actions directly — the handler is where the wire protocol is decided,
 * so a test that skips it would pass while the hall saw nothing.
 */
export function overlayUnderTest(clock = new VirtualClock()) {
  const store = create(captionStore);

  return {
    clock,
    store,
    /** The lines the hall is reading, oldest first. */
    lines: () => store.getState().visibleLines,
    /** What the hall is currently reading, as one string. */
    onScreen: () => store.getState().visibleLines.join(" "),
    /** Push a wire message through the real handler. */
    send: (msg: WsMsg) => handleWsMessage(msg, store, clock.now()),
    /** The next line waiting to scroll on, if any. */
    held: () => store.getState().lineQueue[0]?.text ?? null,
    /** Every complete line still waiting its turn. */
    queued: () => store.getState().lineQueue.map((l) => l.text),
    /** Text that has arrived but has not yet completed a line. */
    building: () => store.getState().building,
    /** Every line the hall has read this session, in order. */
    shown: () => store.getState().shownLines.map((l) => l.text),
    /** Tell the store how many characters a line may hold, as the renderer does. */
    setCharBudget: (n: number) => store.getState().setLineCharBudget(n),
    /** Tell the store how many lines the box can show, as the renderer does. */
    setVisibleLines: (n: number) => store.getState().setMaxVisibleLines(n),
    /** Change a setting the way the operator would. */
    configure: (patch: Record<string, unknown>) =>
      store.setState({ settings: { ...store.getState().settings, ...patch } as never }),
    /** The arrival gaps the tool has observed this session. */
    gaps: () => store.getState().arrivalGaps,
    /** Completed lines the hall never saw. */
    dropped: () => store.getState().linesDropped,
    /**
     * Move the clock WITHOUT running the heartbeat.
     *
     * Not a convenience — it models a real state. Browsers throttle
     * `setInterval` in a backgrounded tab to about once a minute, so an
     * operator who minimises the window gets arrivals with almost no ticks
     * between them. Anything that relies on the tick having run is wrong in
     * that window, and `advance()` would hide it.
     */
    skip: (ms: number) => clock.advance(ms),
    /**
     * Advance the clock, running the caption heartbeat at the same 250ms
     * cadence the renderer uses, so a test exercises the real polling rhythm
     * rather than a single convenient tick at the end.
     */
    advance: (
      ms: number,
      opts: { minDwellMs?: number; clearAfterMs?: number; orphanMs?: number } = {},
    ) => {
      const STEP = 250;
      let remaining = ms;
      while (remaining > 0) {
        const step = Math.min(STEP, remaining);
        store.getState().tickCaptionClock(
          clock.advance(step), opts.minDwellMs, opts.clearAfterMs, opts.orphanMs,
        );
        remaining -= step;
      }
    },
  };
}

/** A `final` wire message — one finished, translated sentence. */
export function final(text: string, raw = "", targetLang = "en-IN"): WsMsg {
  return { type: "final", text, raw, rules_fired: [], target_lang: targetLang };
}
