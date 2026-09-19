import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useStore } from "@/store";
import { OfflineNotice } from "@/components/OfflineNotice";
import { isOverlay } from "@/settings";
import {
  fontStackFor, textPanelCss, panelHeight, panelRadius,
  PANEL_PADDING_X, PANEL_PADDING_Y,
} from "@/settings";
import { renderedLines, LINE_HEIGHT, estimateCharsPerLine, scrollColumn, columnRows } from "@/fit";

// Renders the rolling caption scroll: whole lines entering at the bottom, the
// oldest falling off the top, and the line before last still in front of the
// reader while the new one arrives.
//
// Cutting text into lines uses Range.getClientRects() so reserved-zone
// wrap-around behaves correctly (one rect per visually wrapped line).
//
// `stageScale` lets the operator preview scale the 1920×1080 stage to
// fit a smaller viewport. The overlay renders at 1.0 (1:1 against the
// PP Web Object size).

/**
 * How long one line takes to slide up.
 *
 * Short enough that the eye reads it as the same text moving rather than as
 * new text appearing, long enough not to read as a jump. Not a setting: the
 * operator has no way to judge it from the booth, and every knob that has been
 * exposed on this display has been found set wrong on the day.
 */
const SCROLL_MS = 220;

/**
 * Does this machine ask us not to animate?
 *
 * 🔴 Guarded twice on purpose. `prefers-reduced-motion` is a Chrome 74 media
 * query and vMix runs Chromium 51, where `matchMedia` exists but does not know
 * the feature — an unknown query reports `matches: false`, which is exactly the
 * answer we want there (nobody set a preference, so animate). The `typeof`
 * guard is for the test suite, which runs in plain node with no `window` at
 * all. Neither guard may become an early `return true`: defaulting to "reduced"
 * would silently kill the scroll on every machine that cannot answer.
 */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

type Props = {
  /** 1.0 in overlay mode, fit-to-viewport in operator preview. */
  stageScale: number;
  /**
   * True on the operator's preview, false on the hall screen.
   *
   * It replaces `showGuides`, which every call site passed `false` from the
   * first commit onwards — so everything behind it was dead, including the
   * "speaking …" cue a previous review asked for. The dashed outlines it
   * used to gate are gone: `LiveTab` already draws both rectangles itself,
   * with handles, via `DraggableRect`.
   */
  desk?: boolean;
};

export function CaptionRenderer({ stageScale, desk = false }: Props) {
  const settings         = useStore((s) => s.settings);
  const visibleLines     = useStore((s) => s.visibleLines);
  const partialActive    = useStore((s) => s.partialActive);
  const lastFinalAt      = useStore((s) => s.lastFinalAt);
  const tickCaptionClock = useStore((s) => s.tickCaptionClock);
  const setLineCharBudget = useStore((s) => s.setLineCharBudget);
  const setLineSplitter  = useStore((s) => s.setLineSplitter);
  const setMaxVisibleLines = useStore((s) => s.setMaxVisibleLines);
  const lineCharBudget   = useStore((s) => s.lineCharBudget);
  const linesDropped     = useStore((s) => s.linesDropped);
  const queuedCount      = useStore((s) => s.lineQueue.length);
  // The two OFF-SCREEN rows, as text rather than as counts. They are what the
  // owner described as "3rd 1 is on the queue and 4th one is being
  // constructed": rendered, below the clip, never read by the hall.
  const queuedNext       = useStore((s) => s.lineQueue[0]?.text ?? "");
  const building         = useStore((s) => s.building);
  const buildingLen      = building.length;

  // Scroll the screen on, and take it down once the speaker has stopped.
  //
  // Each line replaces nothing — it pushes — so without this the last lines of
  // a katha stay on the hall screen indefinitely, through the bhajan, the
  // announcements and everything after. Text left up after the speech has
  // stopped is not a caption any more; it is a mistake the audience is still
  // reading.
  //
  // This has to be a timer rather than a check at push time. The old code
  // tested staleness only when the NEXT caption arrived, so the one case that
  // matters — no next caption — was the one case it could never fire on.
  //
  // The component owns the HEARTBEAT; `tickCaptionClock` owns the RULE. Split
  // that way the rule is assertable with a clock a test controls, instead of
  // costing twelve real seconds and a browser to verify.
  //
  // The same heartbeat scrolls a queued line on and releases a line the
  // speaker never finished, so text held behind the screen still reaches the
  // hall when he stops rather than waiting for an arrival that is not coming.
  useEffect(() => {
    if (!lastFinalAt && queuedCount === 0 && buildingLen === 0) return;
    const tick = window.setInterval(
      () => tickCaptionClock(
        Date.now(),
        settings.lineMinDwellSec * 1000,
        settings.captionMaxDwellSec * 1000,
      ),
      250,
    );
    return () => window.clearInterval(tick);
  }, [lastFinalAt, queuedCount, buildingLen, tickCaptionClock,
       settings.lineMinDwellSec, settings.captionMaxDwellSec]);

  // How many lines actually fit, rather than how many were configured.
  //
  // `settings.lines` is a PREFERENCE. The box height and the font size decide
  // what is possible, and when the two disagree the box wins silently — it is
  // `overflow: hidden`. Deriving the count here is what stops the store
  // believing a line is on screen while it is being cut off.
  const maxLines = renderedLines(settings.lines, settings.areaH, settings.fontSize);

  // The strip the scroll gets once the reserved zone has taken its share.
  // The geometry lives in `fit.ts` because it is four branches of arithmetic
  // that decide whether the hall can read a line, and arithmetic inside a
  // component is arithmetic no test ever runs.
  const [textLeft, textWidth] = scrollColumn(settings, maxLines);

  // The store holds the scroll; only this component knows how tall the box is.
  useEffect(() => { setMaxVisibleLines(maxLines); }, [maxLines, setMaxVisibleLines]);

  // Publish what this surface believes, to somewhere a terminal can read it.
  //
  // 🔴 The overlay runs inside vMix's browser input on an unattended machine in
  // Bolton where NOBODY CAN OPEN DEVTOOLS. `data-captions` below answers "what
  // do you think you are showing?" for anyone with a browser, and is useless to
  // the person who actually needs it. This posts the same thing to the server,
  // readable with `curl /api/overlay-status`.
  //
  // Fire-and-forget and failure-swallowing on purpose: a diagnostics channel
  // that can take a caption surface down is worse than no diagnostics channel.
  useEffect(() => {
    const surface = `${isOverlay ? "overlay" : "operator"}-${Math.random().toString(36).slice(2, 8)}`;
    const report = () => {
      const st = useStore.getState();
      fetch("/api/overlay-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          surface,
          // The snapshot: the exact lines the sabha is reading right now.
          onScreen: st.visibleLines.join(" "),
          onScreenLines: st.visibleLines,
          // The sequence. A 5s report cannot catch every line of a scroll that
          // moves every second or two, so the snapshot alone can never answer
          // "did the whole sentence appear, and in order?".
          shown: st.shownLines.map((l) => l.text),
          state: {
            lines: st.settings.lines,
            visible: st.maxVisibleLines,
            budget: st.lineCharBudget,
            queue: st.lineQueue.length,
            building: st.building.length,
            dropped: st.linesDropped,
            panel: st.settings.panelFixed ? "fixed" : (st.settings.textBgOpacity > 0 ? "hugging" : "off"),
            opacity: st.settings.textBgOpacity,
            fontSize: st.settings.fontSize,
          },
        }),
      }).catch(() => { /* never let diagnostics break the hall screen */ });
    };
    report();
    const iv = window.setInterval(report, 5000);
    return () => window.clearInterval(iv);
  }, []);

  // Cut captions where the text ACTUALLY stops fitting — ONE line at a time.
  //
  // 🔴 Three corrections live here, each found by watching a real screen and
  // none by a test.
  //
  // 1. The ceiling came from "about 90 characters fit on a line" — measured at
  //    the mandir PC's own layout and applied to a different one. The shipped
  //    default holds about half that, so long captions overflowed.
  // 2. Dividing the box width by the AVERAGE character width overstates
  //    capacity, because real text breaks at word boundaries and every line
  //    ends ragged.
  // 3. Even corrected, a CHARACTER COUNT is the wrong unit. "Bhagvan
  //    Swaminarayan wrote the Shikshapatri" is far wider than the same number
  //    of characters of short common words. A block measured as two lines
  //    wrapped to three and the hall read a sentence starting from "own hand,
  //    and set down in it...". The store was right; the box disagreed.
  //
  // So the only honest answer lays the real words out in a box with the real
  // font at the real width and adds words until they stop fitting. That needs
  // a DOM, which the store has not got — hence the injected splitter.
  useLayoutEffect(() => {
    const area = areaRef.current;
    if (!area) return;

    const cs = getComputedStyle(area);
    const probe = document.createElement("div");
    probe.style.cssText = [
      "position:absolute", "visibility:hidden", "left:-99999px", "top:0",
      `width:${textWidth}px`,
      `font-size:${cs.fontSize}`, `font-weight:${cs.fontWeight}`,
      `font-family:${cs.fontFamily}`, `line-height:${LINE_HEIGHT}`,
      "white-space:normal", "word-break:normal",
    ].join(";");
    const probeSpan = document.createElement("span");
    probe.appendChild(probeSpan);
    document.body.appendChild(probe);

    const linesFor = (text: string) => {
      probeSpan.textContent = text;
      const range = document.createRange();
      range.selectNodeContents(probeSpan);
      return range.getClientRects().length;
    };

    const split = (text: string): string[] => {
      const words = text.trim().split(/\s+/).filter(Boolean);
      if (words.length === 0) return [];

      const lines: string[] = [];
      let current = "";
      for (const word of words) {
        const candidate = current ? current + " " + word : word;
        // 🔴 ONE line, not `maxLines`. The unit of this display is the line:
        // the store scrolls what this returns onto the screen one item at a
        // time, so an item that is two lines tall would move the screen by two
        // and the reader would lose their place.
        //
        // A single word that overflows on its own still goes up. Degrading to
        // an over-long line beats degrading to a missing word — the same
        // principle as the one-line floor.
        if (!current || linesFor(candidate) <= 1) {
          current = candidate;
        } else {
          lines.push(current);
          current = word;
        }
      }
      if (current) lines.push(current);
      return lines;
    };

    setLineSplitter(split);
    // Keep the character budget roughly right too, since it is what the store
    // falls back to if this ever fails to install. Per LINE now.
    setLineCharBudget(estimateCharsPerLine(textWidth, settings.fontSize));

    return () => {
      setLineSplitter(null);
      if (probe.parentNode) probe.parentNode.removeChild(probe);
    };
  }, [textWidth, settings.fontSize, settings.fontWeight, settings.fontFamily,
      setLineSplitter, setLineCharBudget]);

  const areaRef   = useRef<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement | null>(null);

  // What the column is showing, decided once per CHANGE of the lines rather
  // than once per render.
  //
  // `departed` is the line that has just left the top of the box, kept for one
  // transition so there is something to scroll OUT — without it the remaining
  // lines jump up into the space rather than the old line leaving.
  //
  // 🔴 This is state derived during render, not a ref written by the effect.
  // A ref is written AFTER the DOM is built, so the top slot rendered the
  // PREVIOUS departure — the line leaving the screen was the wrong line.
  // Recomputing it on every render is not the fix either: any unrelated
  // re-render during the 220ms slide (the partial-ellipsis flicking on, say)
  // would find nothing new and blank the departing line out from under the
  // animation. Snapshotting on change is stable through both.
  const [scroll, setScroll] = useState<{ lines: string[]; departed: string; entered: boolean }>(
    { lines: visibleLines, departed: "", entered: false },
  );
  if (scroll.lines !== visibleLines) {
    const prev = scroll.lines;
    // Only a line ENTERING at the bottom scrolls. A trim, a clear, or a resize
    // repositions without pretending it was speech.
    const entered =
      visibleLines.length > 0 &&
      prev.length > 0 &&
      visibleLines[visibleLines.length - 1] !== prev[prev.length - 1];
    setScroll({
      lines: visibleLines,
      // What fell off the top, if the screen was already full.
      departed: entered && prev.length >= visibleLines.length ? prev[0] : "",
      entered,
    });
  }

  const lineBox = settings.fontSize * LINE_HEIGHT;

  // THE SCROLL.
  //
  // The column holds one extra line at the top — the one that has just left —
  // and rests translated up by exactly one line box, so that extra line sits
  // above the clip and is not seen. When a new line arrives we put the column
  // back to 0 with NO transition (the departed line is now visible, everything
  // else is one slot lower, the new line is below the fold), force layout, then
  // animate back to the resting position. The old line rises out of the box and
  // the new one rises into it, together, in one movement.
  //
  // 🔴 The RESTING position is correct with no transition at all. Chromium 51
  // in vMix supports `transform` and `transition` unprefixed, but if either is
  // ever unavailable — a different build, a GPU fallback, a machine we have not
  // seen — the position still lands where it belongs and the scroll degrades to
  // an instant reposition. It never degrades to a wrong line, a blank box, or
  // text stranded mid-slide. That property is why this is a transform on a
  // column rather than a per-line animation.
  useLayoutEffect(() => {
    const col = columnRef.current;
    if (!col) return;

    if (!scroll.entered) {
      col.style.transition = "none";
      col.style.transform = `translateY(${-lineBox}px)`;
      return;
    }

    if (prefersReducedMotion()) {
      // The spec asks for this explicitly, and the degraded path already
      // exists: land on the resting position with no transition. Same lines,
      // same place, no movement.
      col.style.transition = "none";
      col.style.transform = `translateY(${-lineBox}px)`;
      return;
    }

    col.style.transition = "none";
    col.style.transform = "translateY(0px)";
    // Read a layout property to flush the "no transition" position before the
    // animated one is set. Without this the browser coalesces both writes and
    // nothing moves — the classic reason a CSS transition silently does not run.
    void col.offsetHeight;
    col.style.transition = `transform ${SCROLL_MS}ms ease-out`;
    col.style.transform = `translateY(${-lineBox}px)`;
  }, [scroll, lineBox]);

  // Background — transparent (default, PP keys it out) or a solid color.
  const stageBg = settings.bg === "transparent" || !settings.bg ? "transparent" : settings.bg;


  // The departed line rides at the top so it has somewhere to go, and the
  // queued and building lines ride below the fold so the indicator has
  // somewhere to live that is not in front of the sabha. Both boundaries are
  // arithmetic against the clip box, so both live in `fit.ts` under test —
  // see `columnRows` for why the visible slots are padded.
  const column = columnRows(
    scroll.departed, scroll.lines, maxLines, queuedNext, building,
  );

  return (
    <div
      id="stage"
      // What the tool believes, published where it can be read.
      //
      // The overlay runs unattended on a machine in Bolton that nobody can
      // attach a debugger to mid-katha, and "what does it think is on screen,
      // and what is it holding?" has no other answer from outside.
      //
      // A DOM attribute rather than a window global on purpose: globals set by
      // the page are invisible to an extension or an automation tool running in
      // an isolated world, which is exactly the situation this was first needed
      // in. The DOM is shared; `window` is not.
      data-captions={JSON.stringify({
        lines: settings.lines,
        maxLines,
        budget: lineCharBudget,
        onScreen: visibleLines.length,
        queue: queuedCount,
        building: buildingLen,
        dropped: linesDropped,
        // Where the fold is: rows from here down are below the clip. Published
        // so a check from outside can assert the building line is unseen
        // without reading this file, on a machine nobody can attach a debugger
        // to mid-katha.
        fold: column.firstUnseen,
      })}
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
      {/* The fixed rounded panel.
        *
        * One rectangle, always the same size and in the same place, sized to
        * hold exactly `maxLines` of text. A panel that hugs the words changes
        * shape with every line, which is its own kind of movement — and the
        * scroll is meant to be the only thing that moves.
        *
        * Painted BEHIND the caption area rather than as its background, so
        * the area's own geometry and the line layout are untouched by it.
        *
        * `border-radius` and `rgba()` are both fine on vMix's Chromium 51.
        * `inset` and flex `gap` are not, which is why this is left/top/width. */}
      {settings.panelFixed && settings.textBgOpacity > 0 && (
        <div
          style={{
            position: "absolute",
            left: settings.areaX - PANEL_PADDING_X,
            top: settings.areaY - PANEL_PADDING_Y,
            width: settings.areaW + PANEL_PADDING_X * 2,
            height: panelHeight(maxLines, settings.fontSize),
            background: textPanelCss(settings.textBgOpacity),
            borderRadius: panelRadius(settings.fontSize),
            pointerEvents: "none",
          }}
        />
      )}

      {/* Caption area — the window the scroll moves behind.
          `overflow: hidden` is what makes the departed line invisible above
          and the arriving line invisible below.

          🔴 The clip is `maxLines` line boxes tall, NOT `settings.areaH`.
          `areaH` is a preference, the same as `settings.lines`, and it is
          routinely taller than the lines that fit it: at the medium preset it
          is 240px against a 70px line box, i.e. 3.4 lines, while the panel
          covers 2. During the 220ms slide the column sits at translateY(0)
          with three lines inside it, so the third one painted BELOW the panel
          on bare background for the length of every scroll — against the
          whole point of a panel that never changes shape. Clipping to what
          the panel covers is the only thing that keeps the two agreed. */}
      <div
        ref={areaRef}
        style={{
          position: "absolute",
          left: settings.areaX, top: settings.areaY,
          width: settings.areaW, height: maxLines * lineBox,
          fontSize: settings.fontSize,
          fontWeight: settings.fontWeight,
          fontFamily: fontStackFor(settings.fontFamily),
          color: "#fff",
          textShadow: "0 2px 12px rgba(0,0,0,0.85), 0 0 2px rgba(0,0,0,0.6)",
          lineHeight: LINE_HEIGHT,
          overflow: "hidden",
          pointerEvents: "none",
        }}
      >
        <div
          ref={columnRef}
          style={{
            marginLeft: textLeft,
            width: textWidth,
            transform: `translateY(${-lineBox}px)`,
          }}
        >
          {column.rows.map((text, i) => (
            // Keyed by POSITION, not by text. The whole column moves as one
            // piece; keying by text would make React re-create the nodes on
            // every scroll and the transition would restart from nowhere.
            <div
              key={i}
              style={{
                height: lineBox,
                // The words never wrap: the splitter already cut them to fit,
                // and a line that wrapped would move the column by two.
                whiteSpace: "nowrap",
                // The translucent panel behind the words, when it hugs rather
                // than sitting fixed. Inline so it paints the words, not the
                // full width of an empty box.
                background: "transparent",
              }}
            >
              {settings.panelFixed || !(settings.textBgOpacity > 0) ? (
                text
              ) : (
                <span style={{ background: textPanelCss(settings.textBgOpacity) }}>{text}</span>
              )}
              {/* The "still speaking" ellipsis rides the line being BUILT,
                * which is off screen. The hall is never shown a sentence
                * assembling itself — spec #56, user story 3 — and it does not
                * depend on that row having text yet, because a partial can be
                * live before its first character has landed. */}
              {partialActive && i === column.buildingRow && (
                <span style={{ opacity: 0.6 }}> …</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Rendered on EVERY surface, including the hall screen — which is the
        * whole point. See OfflineNotice.tsx and issue #57: a dead server and
        * a quiet katha produced the same empty rectangle for 98 minutes. */}
      <OfflineNotice
        x={settings.areaX}
        y={settings.areaY}
        fontSize={settings.fontSize}
      />

      {/* The "still speaking" cue, for the OPERATOR only.
        *
        * It used to ride the bottom visible line, which meant the sabha
        * watched sentences assemble themselves. It now rides the line being
        * built, and that line is below the clip — so on the hall screen it is
        * correctly invisible, and in the preview it was invisible too, which
        * left the operator with no cue at all.
        *
        * So the cue lives here instead: operator chrome, outside the clip.
        * Nothing about the column or the slide arithmetic changes. */}
      {desk && partialActive && (
        <div style={{
          position: "absolute",
          left: settings.areaX,
          top: settings.areaY - PANEL_PADDING_Y - 34,
          color: "rgba(255,140,0,0.85)",
          fontSize: 22,
          fontFamily: "system-ui, sans-serif",
          pointerEvents: "none",
        }}>
          speaking …
        </div>
      )}
    </div>
  );
}

export type CaptionRendererProps = Props;
