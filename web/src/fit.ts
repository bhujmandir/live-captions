// How much text the caption area can actually display.
//
// The fault this exists to kill: the number of lines the renderer trims to was
// a SETTING, and the height of the box it renders into was a DIFFERENT setting,
// and nothing reconciled them. When the configured line count needed more
// vertical space than the box had, the trim reported success and the box —
// `overflow: hidden` — quietly cut the bottom off.
//
// The bottom line is the NEWEST line. So the fault destroyed the most important
// words on screen, told nobody, and looked from the hall like the captions
// "cutting off". The owner reported exactly that from the live stream.
//
// Everything here is a pure function of numbers. No DOM, no fonts, no globals —
// which is the point: this is the part of the clipping fix that can be proven
// without a browser, so it is deliberately separated from the part that cannot.

/** Multiplier applied to font size to get a line box, matching the renderer's CSS. */
export const LINE_HEIGHT = 1.25;

/**
 * How many whole lines of text the area can display.
 *
 * Always at least 1: an area too short for even one line is a misconfiguration,
 * and showing one clipped line beats showing nothing. A blank caption bar in a
 * full hall has no recovery — degrading to a worse caption beats degrading to
 * none.
 */
export function linesThatFit(
  areaHeight: number,
  fontSize: number,
  lineHeight: number = LINE_HEIGHT,
): number {
  const lineBox = fontSize * lineHeight;
  if (!(lineBox > 0) || !(areaHeight > 0)) return 1;
  return Math.max(1, Math.floor(areaHeight / lineBox));
}

/**
 * The line count the renderer should actually trim to.
 *
 * The smaller of what the operator asked for and what the box can hold, so the
 * two can no longer disagree. This is the single value the renderer reads —
 * `settings.lines` is a preference, not an instruction.
 */
export function renderedLines(
  preference: number,
  areaHeight: number,
  fontSize: number,
  lineHeight: number = LINE_HEIGHT,
): number {
  return Math.min(
    Math.max(1, Math.floor(preference)),
    linesThatFit(areaHeight, fontSize, lineHeight),
  );
}

/**
 * Whether the operator's line preference is more than the box can show.
 *
 * Drives the warning in the toolbar. Without it the operator has to notice
 * clipping by eye on a live output, which is how it reached the sabha.
 */
export function preferenceExceedsArea(
  preference: number,
  areaHeight: number,
  fontSize: number,
  lineHeight: number = LINE_HEIGHT,
): boolean {
  return Math.max(1, Math.floor(preference)) > linesThatFit(areaHeight, fontSize, lineHeight);
}

/**
 * The tallest font size at which `preference` lines still fit the area.
 *
 * What the toolbar warning offers as the fix, so the operator is told what to
 * do rather than only what is wrong.
 */
export function largestFontSizeFor(
  preference: number,
  areaHeight: number,
  lineHeight: number = LINE_HEIGHT,
): number {
  const lines = Math.max(1, Math.floor(preference));
  if (!(areaHeight > 0)) return 0;
  return Math.floor(areaHeight / (lines * lineHeight));
}

// ── Caption timing ───────────────────────────────────────────────────────
//
// The dwell floor is not safe or unsafe in the abstract. It is safe RELATIVE
// to how fast captions actually arrive, and that depends on the speaker, the
// language and the sentence-assembly settings. So the check below takes the
// observed interval rather than a constant somebody measured once and wrote
// down — a hardcoded 4.0s would keep reassuring the operator long after the
// thing it described had changed.

/** Fewer gaps than this and the estimate is noise, not a cadence. */
export const MIN_GAPS_FOR_ESTIMATE = 5;

/**
 * The typical gap between caption arrivals, in ms, or null when too few have
 * been seen to say.
 *
 * Median, not mean: one very long sentence should not drag the estimate
 * upwards, because upwards is the dangerous direction — it makes an unsafe
 * dwell floor look safe.
 */
export function observedArrivalMs(gaps: number[]): number | null {
  if (gaps.length < MIN_GAPS_FOR_ESTIMATE) return null;
  const sorted = [...gaps].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Whether a dwell floor is too close to the observed arrival interval.
 *
 * ⚠️ THIS IS THE TRAP. A floor at or above the interval makes every line wait
 * longer than the gap that feeds it, so lateness grows by the difference on
 * every caption — a minute behind after sixty of them. That is the Day 2
 * Morning fault, rebuilt deliberately.
 *
 * The 0.8 margin exists because arrival gaps vary. A floor at exactly 95% of
 * the median is not "safe": half the gaps are shorter than the median by
 * definition, so it would already be dropping lines it did not need to.
 */
export function dwellFloorIsUnsafe(
  dwellMs: number,
  observedArrival: number | null,
  margin = 0.8,
): boolean {
  if (observedArrival === null) return false;
  return dwellMs >= observedArrival * margin;
}

/**
 * The largest dwell floor that is safe at the observed interval, in ms.
 *
 * Strictly below the threshold, and rounded down to a tenth of a second so the
 * number shown to the operator is one they can actually type and one that is
 * still safe when they do. `floor(interval * margin)` is NOT this value: it
 * sits exactly ON the threshold, so the tool would be recommending a setting
 * its own warning then flags. The test caught that.
 */
export function safeDwellCeilingMs(observedArrival: number, margin = 0.8): number {
  const threshold = observedArrival * margin;
  return Math.max(0, Math.floor((threshold - 1) / 100) * 100);
}

// ── Splitting a caption that does not fit ────────────────────────────────
//
// 🔴 This exists because of a fault that shipped and was reported as fixed.
//
// The line trim drops the OLDEST rendered line to make a caption fit. That was
// right when a caption was a rolling ticker of fragments. It is wrong now that
// a caption is one whole sentence: dropping the oldest line removes the START
// of the sentence. Watched on a real screen, the hall read
//
//     "of every satsangi, across two hundred and twelve verses."
//
// — a sentence beginning halfway through, with no subject, and nothing
// anywhere saying a word had been lost.
//
// Losing the head is worse than losing the tail, because a reader can often
// guess an ending and can never guess a subject. Neither is acceptable. A
// caption that does not fit becomes MORE LINES, shown in succession, and
// every word the swami said reaches the sabha.

/**
 * Split `text` into successive LINES, each at most `maxChars`, breaking only
 * at word boundaries.
 *
 * Named for lines because a line is now the unit of the whole display: one
 * line enters, one leaves. It used to cut a caption into screenfuls, and the
 * old name outlived that by a rewrite.
 *
 * Joining the result with a single space returns the original text: nothing is
 * dropped, ever. A word longer than the whole budget is kept rather than
 * discarded — degrading to an over-long line beats degrading to a missing
 * word, on the same principle as the one-line floor.
 */
export function splitIntoLines(text: string, maxChars: number): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];

  // A nonsense budget must not produce an empty result or an endless loop.
  // One word per line is the safe floor: slower to read, but complete.
  const budget = Number.isFinite(maxChars) && maxChars > 0 ? maxChars : 1;

  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    if (!current) {
      current = word;                       // always take at least one word
    } else if (current.length + 1 + word.length <= budget) {
      current += " " + word;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  return lines;
}

// Average character advance as a fraction of font size.
//
// 🔴 MEASURED, not guessed — and the distinction is the whole lesson here.
// Chrome, the shipped system font stack, 56px at weight 500, on real caption
// English: 24.9px average advance, i.e. 0.445 of the font size. At the shipped
// 1280px caption area that is 51 characters per line.
//
// The fault this replaces came from "about 90 characters fit on a line", which
// was measured at the mandir PC's own layout and then applied to a different
// one without anyone checking. A number is only true for the configuration it
// was taken from.
//
// This is a FALLBACK. The renderer measures the actual font in the actual box
// and overrides it; the estimate exists so the first caption of a session is
// not gambled on an unmeasured number.
const AVG_CHAR_WIDTH_RATIO = 0.445;

/** Characters that fit one line of `areaWidth` at `fontSize`. Never zero. */
export function estimateCharsPerLine(
  areaWidth: number,
  fontSize: number,
  ratio: number = AVG_CHAR_WIDTH_RATIO,
): number {
  const perChar = fontSize * ratio;
  if (!(perChar > 0) || !(areaWidth > 0)) return 1;
  return Math.max(1, Math.floor(areaWidth / perChar));
}

/**
 * The horizontal strip the scroll actually gets, once the reserved zone has
 * taken its share. Returns `[left, width]`, both relative to the caption area.
 *
 * The reserved zone used to be two CSS floats and the paragraph wrapped AROUND
 * them, so the rows above and below the block kept the full width. A scrolled
 * line cannot do that. It is one `nowrap` div, and a float does not shorten it
 * — it shoves its start to the right, which put every line 440px in and ran it
 * off the right-hand edge, cut mid-word, on a build whose whole test suite was
 * green. So the zone narrows the COLUMN instead.
 *
 * Narrowing is all-or-nothing across the rows rather than per row, because
 * lines enter at the bottom and move UP: a line cut to fit a wide row would
 * overflow the moment it scrolled into a narrow one. Cut for the worst row and
 * it is safe in every row it will ever occupy.
 */
export function scrollColumn(
  s: {
    areaX: number; areaY: number; areaW: number;
    blockEnabled: boolean;
    blockX: number; blockY: number; blockW: number; blockH: number;
    fontSize: number;
  },
  maxLines: number,
): [left: number, width: number] {
  const full: [number, number] = [0, s.areaW];
  if (!s.blockEnabled) return full;

  // A block sitting clear above or below the rows the scroll occupies costs
  // nothing. The scroll is only ever `maxLines` tall, not the whole area.
  const scrollBottom = s.areaY + maxLines * s.fontSize * LINE_HEIGHT;
  if (s.blockY >= scrollBottom || s.blockY + s.blockH <= s.areaY) return full;

  const blockLeft  = s.blockX - s.areaX;
  const leftGap    = Math.max(0, Math.min(blockLeft, s.areaW));
  const rightStart = Math.min(s.areaW, Math.max(0, blockLeft + s.blockW));
  const rightGap   = s.areaW - rightStart;

  // A zone so wide it leaves nothing usable is treated as no zone at all — a
  // blank caption bar is the one outcome this tool never degrades to, and a
  // three-character column is a blank caption bar with extra steps.
  if (Math.max(leftGap, rightGap) < s.fontSize * 4) return full;

  return leftGap >= rightGap ? [0, leftGap] : [rightStart, rightGap];
}

// ── The scroll column ────────────────────────────────────────────────────

/** Every row of the scroll column, and where the boundaries fall in it. */
export type ColumnRows = {
  /** The rows themselves, top to bottom, ready to render as-is. */
  rows: string[];
  /**
   * First index that is outside the clip box in EVERY position the column
   * takes. Rows from here down are never seen, in the hall or the preview.
   */
  firstUnseen: number;
  /** Index of the row being constructed — the one the indicator rides. */
  buildingRow: number;
};

/**
 * Lay out the four lines the owner described: two on screen, one queued and
 * ready, one still being built — the last two OFF screen.
 *
 * 🔴 This is arithmetic, not decoration, and getting it wrong paints words
 * assembling themselves in front of the sabha — the one thing user story 3 of
 * spec #56 forbids. Row `i` occupies `[i*box + T, (i+1)*box + T]` against a
 * clip of `[0, maxLines*box]`. `T` is `0` at the instant a slide starts and
 * `-box` at rest, and it is only ever between the two. So:
 *
 *   - at T = 0    rows 0 .. maxLines-1 are inside the clip
 *   - at T = -box rows 1 .. maxLines   are inside the clip
 *
 * The union is 0 .. maxLines, so the first row that is unseen in BOTH is
 * `maxLines + 1`. That is where the queued and building rows have to start.
 *
 * 🔴 Which is why the visible slots are PADDED. A screen holding one line of a
 * two-line box would otherwise put the building row at index 2 — inside the
 * clip, in the hall, exactly the case a full screen never exercises and a
 * demo never shows. The empty rows cost a `<div>` and remove the trap.
 *
 * A snapshot taken before the operator shrank the line count can hold more
 * lines than `maxLines`, so the boundary takes whichever is larger. Padding
 * short and never truncating long: the visible rows are the hall's, and this
 * function's job is only to keep the unseen ones unseen.
 */
export function columnRows(
  departed: string,
  visible: string[],
  maxLines: number,
  queued: string,
  building: string,
): ColumnRows {
  const slots = Math.max(1, Math.floor(maxLines));
  const rows = [departed, ...visible];
  while (rows.length < slots + 1) rows.push("");

  const firstUnseen = rows.length;
  rows.push(queued, building);

  return { rows, firstUnseen, buildingRow: rows.length - 1 };
}
