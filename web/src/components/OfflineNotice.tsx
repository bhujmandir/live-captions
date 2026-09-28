import { hallNotice } from "@/link-health";
import { useSocketState } from "@/use-socket-state";

// The one thing the hall screen could not say.
//
// Issue #57: 98 minutes with no server, and the caption area looked
// identical to a quiet moment in the katha — empty. Nobody in the sabha,
// and nobody walking past the desk, had any way to tell.
//
// Everything about this is shaped by where it renders: inside vMix's
// Chromium 51, composited over the live programme feed, in front of a
// hall. So —
//
//   * it is small and sits at the caption area's own top-left, not a
//     full-screen panel over the feed;
//   * `rgba()` and `border-radius` only. No `inset`, no flex `gap`, no
//     `#RRGGBBAA` — Chromium 51 drops all three silently;
//   * it waits out the grace window in `link-health.ts`, so the 1500ms
//     reconnect cycle cannot strobe it;
//   * the decision itself is `hallNotice`, which is pure and tested. What
//     is left here is only the wiring, because none of this file can be
//     run in the browser it ships to.

// The pill's proportions, all relative to the caption font so it scales with
// whatever the hall is set to and always reads as secondary to the words.
// 0.42 is the largest that stayed clearly smaller than a caption line at the
// 1080p sizes actually used (72-140px); below ~18px it stops being readable
// from the back of the hall at all, hence the floor.
const SIZE_RATIO      = 0.42;
const MIN_SIZE_PX     = 18;
const PAD_Y_RATIO     = 0.3;
const PAD_X_RATIO     = 0.7;
const RADIUS_RATIO    = 0.4;

// NOT `system-ui`. That generic arrived in Chrome 56 and vMix's browser input
// is Chromium 51, where it is dropped and the text falls to the browser's
// default serif — on the one surface the sabha reads. Named families only.
const NOTICE_FONT = "Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif";

type Props = {
  /** Caption area's left edge, in stage pixels. */
  x: number;
  /** Caption area's top edge, in stage pixels. */
  y: number;
  /** The caption font size, so the notice reads as secondary to the words. */
  fontSize: number;
};

export function OfflineNotice({ x, y, fontSize }: Props) {
  const socket = useSocketState();
  if (hallNotice(socket) !== "offline") return null;

  const size = Math.max(MIN_SIZE_PX, Math.round(fontSize * SIZE_RATIO));

  return (
    <div
      data-offline-notice="1"
      style={{
        position: "absolute",
        left: x,
        top: y,
        padding: `${Math.round(size * PAD_Y_RATIO)}px ${Math.round(size * PAD_X_RATIO)}px`,
        background: "rgba(0,0,0,0.62)",
        color: "rgba(240,90,80,0.95)",
        fontSize: size,
        fontFamily: NOTICE_FONT,
        fontWeight: 600,
        letterSpacing: "0.08em",
        borderRadius: Math.round(size * RADIUS_RATIO),
        pointerEvents: "none",
      }}
    >
      CAPTIONS OFFLINE
    </div>
  );
}
