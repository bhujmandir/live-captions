// The clipping fix, proven without a browser.
//
// The owner's report from the live stream: "sometimes it cuts off ... u can see
// it cut off at bottom". These cases pin the rule that makes that impossible.

import { describe, it, expect } from "vitest";
import {
  LINE_HEIGHT,
  linesThatFit,
  renderedLines,
  preferenceExceedsArea,
  largestFontSizeFor,
  observedArrivalMs,
  dwellFloorIsUnsafe,
  safeDwellCeilingMs,
  splitIntoLines,
  estimateCharsPerLine,
  scrollColumn,
  columnRows,
} from "./fit";
import { DEFAULTS, PRESETS, panelHeight, PANEL_PADDING_Y } from "./settings";

describe("linesThatFit", () => {
  it("counts whole lines only — a half-visible line is a clipped line", () => {
    // 240px tall, 56px font, 1.25 line height => 70px per line => 3.43 lines.
    expect(linesThatFit(240, 56)).toBe(3);
  });

  it("shrinks as the font grows", () => {
    expect(linesThatFit(240, 40)).toBe(4);
    expect(linesThatFit(240, 56)).toBe(3);
    expect(linesThatFit(240, 100)).toBe(1);
  });

  it("never returns zero, even for an area too short for one line", () => {
    // A blank caption bar in a full hall has no recovery. One clipped line
    // still says something; nothing says nothing.
    expect(linesThatFit(10, 56)).toBe(1);
    expect(linesThatFit(0, 56)).toBe(1);
  });

  it("survives nonsense input rather than propagating NaN to the renderer", () => {
    expect(linesThatFit(240, 0)).toBe(1);
    expect(linesThatFit(-100, 56)).toBe(1);
    expect(linesThatFit(NaN, 56)).toBe(1);
    expect(linesThatFit(240, NaN)).toBe(1);
  });
});

describe("renderedLines — the value the renderer actually trims to", () => {
  it("honours the operator's preference when it fits", () => {
    expect(renderedLines(2, 240, 56)).toBe(2);
  });

  it("caps the preference at what the box can hold", () => {
    // THE BUG. Line count and box height used to be independent settings, and
    // when they disagreed the trim believed the caption fitted while the box
    // cut the bottom — the newest words — off silently.
    expect(renderedLines(3, 240, 100)).toBe(1);
    expect(renderedLines(10, 240, 56)).toBe(3);
  });

  it("never returns less than one line", () => {
    expect(renderedLines(0, 240, 56)).toBe(1);
    expect(renderedLines(-5, 240, 56)).toBe(1);
  });
});

describe("no caption can render taller than its area", () => {
  // The regression test the spec asks for. Sweep the whole font range the
  // operator can scrub to, against every shipped layout, and assert the
  // rendered height never exceeds the available height.
  const layouts = [
    { name: "defaults", areaH: DEFAULTS.areaH },
    ...Object.entries(PRESETS).map(([name, p]) => ({ name, areaH: p.areaH as number })),
  ];

  for (const layout of layouts) {
    it(`fits at every font size the toolbar allows — ${layout.name}`, () => {
      for (let fontSize = 8; fontSize <= 200; fontSize++) {
        for (const preference of [1, 2, 3, 5, 10]) {
          const lines = renderedLines(preference, layout.areaH, fontSize);
          const height = lines * fontSize * LINE_HEIGHT;

          // It either fits outright, or it is the documented single-line floor
          // for an area too short to hold even one line — where showing one
          // clipped line beats showing nothing at all.
          expect(
            height <= layout.areaH || lines === 1,
            `${layout.name}: ${preference} lines wanted at ${fontSize}px gave ${lines} lines = ${height}px in ${layout.areaH}px`,
          ).toBe(true);
        }
      }
    });
  }
});

describe("telling the operator", () => {
  it("flags a preference the area cannot hold", () => {
    expect(preferenceExceedsArea(3, 240, 100)).toBe(true);
  });

  it("stays quiet when the preference fits", () => {
    expect(preferenceExceedsArea(3, 240, 56)).toBe(false);
    expect(preferenceExceedsArea(1, 240, 56)).toBe(false);
  });

  it("names the font size that would make the preference fit", () => {
    // 240px, 3 lines, 1.25 line height => 64px per line box => 64px font.
    expect(largestFontSizeFor(3, 240)).toBe(64);
    // And that answer must itself be true.
    expect(linesThatFit(240, largestFontSizeFor(3, 240))).toBeGreaterThanOrEqual(3);
  });

  it("gives an answer that fits for every preference and area it can be asked about", () => {
    for (const areaH of [120, 240, 340, 500]) {
      for (const preference of [1, 2, 3, 4, 5]) {
        const font = largestFontSizeFor(preference, areaH);
        if (font >= 8) {
          expect(linesThatFit(areaH, font)).toBeGreaterThanOrEqual(preference);
        }
      }
    }
  });
});

describe("the shipped layouts", () => {
  // These are what the mandir PC actually runs. If a preset ships clipping,
  // that is a fault in the preset, and this is where it gets caught.
  it("every preset shows the line count it advertises", () => {
    for (const [name, p] of Object.entries(PRESETS)) {
      const fits = linesThatFit(p.areaH as number, p.fontSize as number);
      expect(fits, `preset "${name}" clips`).toBeGreaterThanOrEqual(p.lines as number);
    }
  });

  it("the default layout shows the line count it advertises", () => {
    expect(linesThatFit(DEFAULTS.areaH, DEFAULTS.fontSize)).toBeGreaterThanOrEqual(DEFAULTS.lines);
  });
});

describe("the settled display shape", () => {
  // The owner settled this after watching the captions live: "2 lines showing
  // at a time". Two lines is the shape, not a per-preset ceiling — and it is
  // what CAPTION_SENTENCE_MAX_CHARS (180) is set against, so a preset
  // disagreeing would put the server's cap and the display back out of step.
  it("ships two lines everywhere", () => {
    expect(DEFAULTS.lines).toBe(2);
    for (const [name, p] of Object.entries(PRESETS)) {
      expect(p.lines, `preset "${name}" is not two lines`).toBe(2);
    }
  });
});

describe("the dwell trap", () => {
  // ⚠️ A dwell floor at or above the line arrival interval makes every line
  // wait longer than the gap that feeds it. Lateness then grows by the
  // difference on every caption — a minute behind after sixty. This is Day 2
  // Morning rebuilt deliberately, and it is the reason the knob carries a
  // warning rather than just a range.
  it("says nothing until it has seen enough arrivals to have an opinion", () => {
    // A confident number from three samples would be worse than silence.
    expect(observedArrivalMs([4000, 4000])).toBe(null);
    expect(observedArrivalMs([])).toBe(null);
    expect(dwellFloorIsUnsafe(9000, observedArrivalMs([4000, 4000]))).toBe(false);
  });

  it("estimates the interval from what was actually observed", () => {
    expect(observedArrivalMs([4000, 4000, 4000, 4000, 4000])).toBe(4000);
  });

  it("is not dragged upwards by one long sentence", () => {
    // Upwards is the DANGEROUS direction: it would make an unsafe floor look
    // safe. A median resists the outlier; a mean would not.
    const gaps = [3800, 4000, 4200, 3900, 4100, 40_000];
    expect(observedArrivalMs(gaps)).toBeLessThan(5000);
  });

  it("flags a floor that the speaker's cadence cannot support", () => {
    const arrival = observedArrivalMs([4000, 4000, 4000, 4000, 4000]);

    expect(dwellFloorIsUnsafe(2000, arrival)).toBe(false);  // the shipped default
    expect(dwellFloorIsUnsafe(3200, arrival)).toBe(true);   // at the margin
    expect(dwellFloorIsUnsafe(5000, arrival)).toBe(true);   // outright slower than speech
  });

  it("leaves a margin rather than approving a floor at the median", () => {
    // Half of all gaps are shorter than the median by definition, so a floor
    // AT the median is already dropping lines it need not have dropped.
    const arrival = 4000;
    expect(dwellFloorIsUnsafe(4000, arrival)).toBe(true);
    expect(dwellFloorIsUnsafe(3900, arrival)).toBe(true);
  });

  it("names a ceiling that is itself safe", () => {
    for (const arrival of [1500, 2500, 4000, 6000]) {
      const ceiling = safeDwellCeilingMs(arrival);
      expect(dwellFloorIsUnsafe(ceiling, arrival)).toBe(false);
      expect(ceiling).toBeLessThan(arrival);
    }
  });

  it("ships a default that is safe at the per-line interval measured on this speaker", () => {
    // 🔴 Against the LINE interval, not the caption interval. Captions arrived
    // every 3.83s on Day 3 and one caption is commonly two lines, so the gap
    // that feeds the scroll is about 1.9s. Checking the floor against 3.83s
    // would pass a setting that cannot keep up — the exact shape of mistake
    // this project keeps making: a number measured on one thing, applied to
    // another.
    expect(dwellFloorIsUnsafe(DEFAULTS.lineMinDwellSec * 1000, 1900)).toBe(false);
  });

  it("ships a floor at or above the broadcast minimum, so a line is never flashed away", () => {
    expect(DEFAULTS.lineMinDwellSec * 1000).toBeGreaterThanOrEqual(1500);
  });

  it("ships one setting for the maximum dwell and the silence clear", () => {
    // Two knobs for one question can disagree, which is exactly how the line
    // count and the box height ended up clipping text at each other.
    expect(DEFAULTS.captionMaxDwellSec).toBeGreaterThan(DEFAULTS.lineMinDwellSec);
  });
});

describe("splitting a caption that cannot fit", () => {
  // THE BUG THE TESTS MISSED, and the screen showed in one look.
  //
  // A sentence longer than two lines had its FRONT silently removed by the
  // line trim, so the hall read "of every satsangi, across two hundred and
  // twelve verses." — a sentence beginning halfway through, with no subject
  // and no indication anything was missing.
  //
  // Losing the head of a sentence is worse than losing the tail: the tail can
  // often be guessed, the subject cannot. Neither is acceptable. A caption
  // that does not fit becomes MORE BLOCKS, and every word reaches the sabha.
  it("leaves a caption that already fits alone", () => {
    expect(splitIntoLines("The sant explains that this is not a rulebook.", 102))
      .toEqual(["The sant explains that this is not a rulebook."]);
  });

  it("splits a caption too long for the space into successive lines", () => {
    const long = "Bhagvan Swaminarayan wrote the Shikshapatri with his own hand, "
      + "and set down in it the conduct expected of every satsangi.";
    const pieces = splitIntoLines(long, 60);

    expect(pieces.length).toBeGreaterThan(1);
    for (const b of pieces) expect(b.length).toBeLessThanOrEqual(60);
  });

  it("loses not one word", () => {
    // The whole point. Every word the swami said reaches the screen.
    const long = "A cursed intellect turns even good counsel into a grievance, "
      + "and that is the nature the shastra warns against; it asks us to guard "
      + "the mind before it hardens.";

    expect(splitIntoLines(long, 50).join(" ")).toBe(long);
  });

  it("never breaks a word in half", () => {
    const long = "Shikshapatri Bhashya Swaminarayan Patotsav Vachanamrut Satsangi";
    for (const piece of splitIntoLines(long, 25)) {
      expect(long).toContain(piece);
      expect(piece.startsWith(" ")).toBe(false);
      expect(piece.endsWith(" ")).toBe(false);
    }
  });

  it("keeps a single word longer than the budget rather than dropping it", () => {
    // Degrading to a clipped word beats degrading to no word. A blank or
    // silently-shortened caption bar in a full hall has no recovery.
    const pieces = splitIntoLines("Sarvamangalamangalye", 5);

    expect(pieces.join(" ")).toContain("Sarvamangalamangalye");
    expect(pieces.length).toBeGreaterThan(0);
  });

  it("survives an empty or nonsense budget rather than looping forever", () => {
    expect(splitIntoLines("", 100)).toEqual([]);
    expect(splitIntoLines("   ", 100)).toEqual([]);
    expect(splitIntoLines("Jay Swaminarayan.", 0).join(" ")).toContain("Jay");
    expect(splitIntoLines("Jay Swaminarayan.", -5).join(" ")).toContain("Jay");
  });

  it("splits the real caption that was truncated on screen into lines that fit", () => {
    // Measured in Chrome against the shipped default layout: 1280px wide,
    // 56px font, 24.9px average advance => 51 characters per line, 102 for
    // two. The server was allowing 180.
    const truncated = "Bhagvan Swaminarayan wrote the Shikshapatri with his own hand, "
      + "and set down in it the conduct expected of every satsangi, "
      + "across two hundred and twelve verses.";

    const pieces = splitIntoLines(truncated, 102);

    expect(pieces.length).toBe(2);
    for (const b of pieces) expect(b.length).toBeLessThanOrEqual(102);
    expect(pieces[0]).toContain("Bhagvan Swaminarayan wrote");
    expect(pieces.join(" ")).toBe(truncated);
  });
});

describe("estimating how many characters fit", () => {
  // A fallback for when nothing has measured yet. The renderer measures the
  // real font in the real box and overrides this — the estimate exists so the
  // FIRST caption of a session is not gambled on a number nobody checked.
  it("is calibrated against a real measurement, not a guess", () => {
    // Chrome, system font, 56px, weight 500: 24.9px average advance.
    // 1280 / 24.9 = 51 characters per line.
    expect(estimateCharsPerLine(1280, 56)).toBeGreaterThanOrEqual(48);
    expect(estimateCharsPerLine(1280, 56)).toBeLessThanOrEqual(54);
  });

  it("does not repeat the 90-characters mistake", () => {
    // The number this whole fault came from. It was measured at the mandir
    // PC's own layout and then applied to a different one without checking.
    expect(estimateCharsPerLine(1280, 56)).toBeLessThan(70);
  });

  it("scales the right way with the box and the font", () => {
    expect(estimateCharsPerLine(2560, 56)).toBeGreaterThan(estimateCharsPerLine(1280, 56));
    expect(estimateCharsPerLine(1280, 28)).toBeGreaterThan(estimateCharsPerLine(1280, 56));
  });

  it("never returns zero, whatever it is asked", () => {
    expect(estimateCharsPerLine(0, 56)).toBeGreaterThan(0);
    expect(estimateCharsPerLine(1280, 0)).toBeGreaterThan(0);
    expect(estimateCharsPerLine(NaN, NaN)).toBeGreaterThan(0);
  });
});

describe("the reserved zone, under a scroll that cannot wrap", () => {
  // The block model wrapped text AROUND the reserved zone with CSS floats.
  // A scrolled line is one `nowrap` div: a float does not shorten it, it moves
  // its start, and every line ran off the right edge cut mid-word — while the
  // whole suite was green. These are the assertions that were missing.
  const area = { areaX: 320, areaY: 780, areaW: 1280, fontSize: 56 };
  const zone = (patch: Record<string, unknown> = {}) => ({
    ...area,
    blockEnabled: true, blockX: 760, blockY: 860, blockW: 400, blockH: 120,
    ...patch,
  });

  it("costs nothing at all when it is switched off", () => {
    expect(scrollColumn({ ...zone(), blockEnabled: false }, 2)).toEqual([0, 1280]);
  });

  it("costs nothing when the block never reaches the rows the scroll uses", () => {
    // Two lines at 56px occupy 780–920. A block starting at 1000 is below them.
    expect(scrollColumn(zone({ blockY: 1000 }), 2)).toEqual([0, 1280]);
    // And one that ends before the area starts is above them.
    expect(scrollColumn(zone({ blockY: 600, blockH: 100 }), 2)).toEqual([0, 1280]);
  });

  it("takes the wider clear side, and reports where it starts", () => {
    // Block 760..1160 inside area 320..1600 — 440 left, 440 right. Tie goes left.
    expect(scrollColumn(zone(), 2)).toEqual([0, 440]);
    // Push the block to the left edge and the right-hand gap wins: the block
    // sits 100..500 inside the area, so the column starts at 500 and runs 780.
    expect(scrollColumn(zone({ blockX: 420 }), 2)).toEqual([500, 780]);
  });

  it("never returns a column that starts inside the block", () => {
    for (const blockX of [200, 320, 700, 1100, 1500, 1900]) {
      const [left, width] = scrollColumn(zone({ blockX }), 2);
      const blockLeft = blockX - area.areaX;
      const overlaps = left < blockLeft + 400 && left + width > blockLeft;
      // Either the column dodges the block, or the block was too wide to dodge
      // and the zone was abandoned rather than leaving an unreadable sliver.
      expect(overlaps ? width : 0).toBe(overlaps ? 1280 : 0);
    }
  });

  it("abandons a zone that would leave a sliver, rather than blanking the bar", () => {
    // A block covering all but 100px either side: 100 < 4 chars at 56px.
    const [left, width] = scrollColumn(zone({ blockX: 420, blockW: 1080 }), 2);
    expect([left, width]).toEqual([0, 1280]);
  });

  it("never returns a zero or negative width, whatever the operator types", () => {
    for (const patch of [
      { blockX: -5000 }, { blockX: 9000 }, { blockW: 99999 },
      { blockW: 0 }, { blockX: 320, blockW: 1280 },
    ]) {
      const [, width] = scrollColumn(zone(patch), 2);
      expect(width).toBeGreaterThan(0);
    }
  });
});

describe("the panel and the clip box agree", () => {
  // The invariant that broke. `areaH` is a PREFERENCE — the operator's box —
  // and it is routinely taller than the lines that fit inside it. The panel is
  // sized from `maxLines`, so when the clip was sized from `areaH` the two
  // disagreed, and the disagreement only showed for the 220ms of a slide: the
  // arriving third line sat inside the clip but below the panel, painting on
  // bare background. Every preset has to satisfy this, not just the one we
  // happened to open.
  const configs: Array<[string, { lines: number; areaH: number; fontSize: number }]> = [
    ["defaults", DEFAULTS],
    ["small", { ...DEFAULTS, ...PRESETS.small }],
    ["medium", { ...DEFAULTS, ...PRESETS.medium }],
    ["large", { ...DEFAULTS, ...PRESETS.large }],
    // The mandir PC's live URL, which is none of the presets.
    ["the live mandir config", { lines: 2, areaH: 200, fontSize: 64 }],
  ];

  for (const [name, cfg] of configs) {
    it(`covers every clipped row at ${name}`, () => {
      const maxLines = renderedLines(cfg.lines, cfg.areaH, cfg.fontSize);
      const clip = maxLines * cfg.fontSize * LINE_HEIGHT;
      // The panel starts PANEL_PADDING_Y above the area, so what it covers
      // BELOW the first row is its height less that overhang.
      const covered = panelHeight(maxLines, cfg.fontSize) - PANEL_PADDING_Y;
      expect(covered).toBeGreaterThanOrEqual(clip);
    });
  }

  it("clips to the panel and not to the operator's box", () => {
    // Directly the failing case: the medium preset holds 3.4 line boxes in the
    // area, and shows 2. A clip of areaH would show a third line mid-slide.
    const cfg = { ...DEFAULTS, ...PRESETS.medium };
    const maxLines = renderedLines(cfg.lines, cfg.areaH, cfg.fontSize);
    expect(maxLines * cfg.fontSize * LINE_HEIGHT).toBeLessThan(cfg.areaH);
  });
});

describe("the line being built stays off the hall screen", () => {
  // The clip box, in line boxes, is `maxLines` tall and starts at 0. The
  // column rests at -1 and slides from 0, so a row is SEEN if it overlaps
  // [0, maxLines] at either end of the travel. Written out here rather than
  // taken from the helper, so the test can disagree with it.
  function seenRows(rows: number, maxLines: number): number[] {
    const seen: number[] = [];
    for (let i = 0; i < rows; i++) {
      const atStart = i < maxLines;              // T = 0
      const atRest  = i >= 1 && i <= maxLines;   // T = -1 line box
      if (atStart || atRest) seen.push(i);
    }
    return seen;
  }

  it("puts the queued and building rows past everything the clip can show", () => {
    const c = columnRows("gone", ["one", "two"], 2, "queued", "building…");

    expect(seenRows(c.rows.length, 2)).not.toContain(c.firstUnseen);
    expect(seenRows(c.rows.length, 2)).not.toContain(c.buildingRow);
  });

  it("keeps them off screen when the screen is not yet full", () => {
    // 🔴 The case that a full screen never exercises. One line into a katha
    // the box holds a single line, and an unpadded append would drop the
    // building row straight into the second visible slot.
    const c = columnRows("", ["only one so far"], 2, "queued", "building…");
    const seen = seenRows(c.rows.length, 2);

    expect(seen).not.toContain(c.buildingRow);
    expect(seen).not.toContain(c.firstUnseen);
    expect(c.rows[c.buildingRow]).toBe("building…");
  });

  it("keeps them off screen on a completely empty screen", () => {
    const c = columnRows("", [], 2, "", "the very first words");
    const seen = seenRows(c.rows.length, 2);

    expect(seen).not.toContain(c.buildingRow);
    expect(seen.every((i) => c.rows[i] === "")).toBe(true);
  });

  it("holds the boundary at every line count an operator can pick", () => {
    for (let maxLines = 1; maxLines <= 6; maxLines++) {
      for (let onScreen = 0; onScreen <= maxLines; onScreen++) {
        const visible = Array.from({ length: onScreen }, (_, i) => `line ${i}`);
        const c = columnRows("departed", visible, maxLines, "queued", "building");
        const seen = seenRows(c.rows.length, maxLines);

        expect(seen).not.toContain(c.buildingRow);
        expect(seen.filter((i) => i >= c.firstUnseen)).toEqual([]);
      }
    }
  });

  it("does not lose lines when the snapshot is taller than the box", () => {
    // The operator drops the line count mid-katha. The snapshot still holds
    // three; none of them may vanish, and the building row still may not show.
    const c = columnRows("d", ["a", "b", "c"], 2, "queued", "building");

    expect(c.rows.slice(1, 4)).toEqual(["a", "b", "c"]);
    expect(seenRows(c.rows.length, 2)).not.toContain(c.buildingRow);
  });

  it("still renders the visible lines exactly as they were given", () => {
    const c = columnRows("gone", ["one", "two"], 2, "queued", "building");

    expect(c.rows.slice(0, 3)).toEqual(["gone", "one", "two"]);
  });

  it("gives the indicator a row to ride even with nothing built yet", () => {
    // `partialActive` can be true before a single character of the next line
    // has landed. The row has to exist anyway, or the indicator falls back
    // onto the last row that does — which is a line the hall is reading.
    const c = columnRows("", ["one", "two"], 2, "", "");

    expect(c.buildingRow).toBeGreaterThan(2);
    expect(seenRows(c.rows.length, 2)).not.toContain(c.buildingRow);
  });
});
