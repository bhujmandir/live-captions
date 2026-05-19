import { useEffect, useLayoutEffect, useRef } from "react";
import { useStore } from "@/store";
import { fontStackFor, type Settings } from "@/settings";

// Renders the caption block. The line-trim algorithm uses
// Range.getClientRects() so reserved-zone wrap-around behaves correctly
// on the ProPresenter overlay path (one rect per visually wrapped line).
//
// `stageScale` lets the operator preview scale the 1920×1080 stage to
// fit a smaller viewport. The overlay renders at 1.0 (1:1 against the
// PP Web Object size).

type Props = {
  /** 1.0 in overlay mode, fit-to-viewport in operator preview. */
  stageScale: number;
  /** Show subtle outlines of the caption area + reserved block. Operator only. */
  showGuides?: boolean;
};

export function CaptionRenderer({ stageScale, showGuides = false }: Props) {
  const settings        = useStore((s) => s.settings);
  const captionText     = useStore((s) => s.captionText);
  const partialActive   = useStore((s) => s.partialActive);
  const setCaptionMutate = useStore((s) => s.clearCaption); // unused alias — keep ref to avoid double-render thrash
  void setCaptionMutate;

  // We render the entire caption into a single <span>, then trim it
  // backwards in characters until it fits the configured max-lines.
  // Reserved-zone floats (two of them) sit at top of the area; their
  // shape-outside makes the text wrap around the centred block.
  const textRef = useRef<HTMLSpanElement | null>(null);
  const areaRef = useRef<HTMLDivElement | null>(null);

  // Hold the actual on-screen string so we can slice off the oldest
  // visual lines without losing the source-of-truth in the store. The
  // store keeps up to ~6000 chars; we only render whatever fits.
  const visibleRef = useRef<string>("");

  // Re-trim whenever text/settings change AND after layout.
  useLayoutEffect(() => {
    visibleRef.current = captionText;
    if (textRef.current) textRef.current.textContent = visibleRef.current;
    trimToMaxLines(textRef.current, settings.lines);
  }, [captionText, settings.lines, settings.fontSize, settings.fontWeight,
       settings.fontFamily,
       settings.areaW, settings.areaH, settings.blockEnabled,
       settings.blockX, settings.blockY, settings.blockW, settings.blockH,
       stageScale]);

  // Background — transparent (default, PP keys it out) or a solid color.
  const stageBg = settings.bg === "transparent" || !settings.bg ? "transparent" : settings.bg;

  // Reserved-zone floats. Two floats so text wraps on BOTH sides of the
  // centred block (one CSS float can only wrap on one side). The shapes
  // describe a rectangle taking the same area as the block, but each
  // float lives on the opposite side of the area. The actual visible
  // gap (the block) is the union.
  const blockTop  = settings.blockY - settings.areaY;
  const blockLeft = settings.blockX - settings.areaX;
  const blockH    = settings.blockH;
  const blockW    = settings.blockW;
  const areaW     = settings.areaW;

  return (
    <div
      id="stage"
      style={{
        position: "absolute",
        left: 0, top: 0,
        width: 1920, height: 1080,
        transform: `scale(${stageScale})`,
        transformOrigin: "top left",
        background: stageBg,
        pointerEvents: "none",
      }}
    >
      {/* Caption area */}
      <div
        ref={areaRef}
        style={{
          position: "absolute",
          left: settings.areaX, top: settings.areaY,
          width: settings.areaW, height: settings.areaH,
          fontSize: settings.fontSize,
          fontWeight: settings.fontWeight,
          fontFamily: fontStackFor(settings.fontFamily),
          color: "#fff",
          textShadow: "0 2px 12px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.6)",
          lineHeight: 1.25,
          overflow: "hidden",
          pointerEvents: "none",
        }}
      >
        {/* Optional reserved-zone floats — two of them so text wraps on
            both sides of the centred block. */}
        {settings.blockEnabled && (
          <>
            <div style={{
              float: "left",
              width: blockLeft,
              height: blockH,
              marginTop: blockTop,
              shapeOutside: `inset(0 0 0 0)`,
            }} />
            <div style={{
              float: "right",
              width: Math.max(0, areaW - (blockLeft + blockW)),
              height: blockH,
              marginTop: blockTop,
              shapeOutside: `inset(0 0 0 0)`,
            }} />
          </>
        )}
        <span ref={textRef} />
        {partialActive && <span style={{ opacity: 0.6, marginLeft: 4 }}> …</span>}
      </div>

      {/* Operator-only visual guides — never rendered in overlay mode. */}
      {showGuides && (
        <>
          <div style={{
            position: "absolute",
            left: settings.areaX, top: settings.areaY,
            width: settings.areaW, height: settings.areaH,
            outline: "1px dashed rgba(255,140,0,0.55)",
            pointerEvents: "none",
          }} />
          {settings.blockEnabled && (
            <div style={{
              position: "absolute",
              left: settings.blockX, top: settings.blockY,
              width: settings.blockW, height: settings.blockH,
              outline: "1px dashed rgba(33,150,243,0.55)",
              pointerEvents: "none",
            }} />
          )}
        </>
      )}
    </div>
  );
}

// Line-based trim — drops the OLDEST whole rendered line at a time
// using Range.getClientRects(), which correctly accounts for
// reserved-zone wrap-around (one rect per visually wrapped line).
function trimToMaxLines(node: HTMLElement | null, maxLines: number) {
  if (!node) return;
  if (!node.firstChild) return;
  // Iterate at most 20× — defensive against pathological layouts that
  // never reach the target line count.
  for (let i = 0; i < 20; i++) {
    const tn = node.firstChild as Text;
    if (!tn || tn.nodeType !== Node.TEXT_NODE) break;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = range.getClientRects();
    if (rects.length <= Math.max(1, maxLines)) break;
    const targetTop = rects[rects.length - maxLines].top;
    let lo = 0, hi = tn.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const rg = document.createRange();
      rg.setStart(tn, mid);
      rg.setEnd(tn, mid);
      if (rg.getBoundingClientRect().top < targetTop - 0.5) lo = mid + 1;
      else hi = mid;
    }
    // Snap forward to a word boundary so we never cut mid-word.
    while (lo < tn.length && !/\s/.test(tn.data[lo])) lo++;
    while (lo < tn.length &&  /\s/.test(tn.data[lo])) lo++;
    if (lo === 0 || lo >= tn.length) break;
    node.textContent = (tn.data || "").slice(lo);
  }
}

export type CaptionRendererProps = Props;
