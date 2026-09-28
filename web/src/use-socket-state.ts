import { useEffect, useState } from "react";
import { useStore } from "./store";
import type { SocketState } from "./link-health";

// How often the "how long has it been down" clock is re-read while the
// socket is shut. Only runs while it IS shut, so the hall screen is not
// paying for a timer during a normal katha.
const TICK_MS = 500;

/**
 * The socket, plus how long it has been in that state.
 *
 * Both `hallNotice` and `deskNotice` need this and neither can compute it:
 * they are pure, and "for how long" is the one thing a pure function of the
 * current state cannot see. Issue #57 — the desk banner keyed straight off
 * `conn === "closed"` and blinked off every time `api.ts` retried.
 */
export function useSocketState(): SocketState {
  const conn = useStore((s) => s.conn);
  const down = conn !== "open";

  const [downSince, setDownSince] = useState<number | null>(() => (down ? Date.now() : null));
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!down) { setDownSince(null); return; }
    setDownSince((prev) => prev ?? Date.now());
  }, [down]);

  useEffect(() => {
    if (!down) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [down]);

  return { conn, downSince, now };
}
