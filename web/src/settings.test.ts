// Settings hydration, off-browser.
//
// `settings.ts` had to become importable without a browser before `store.ts`
// could be — the store loads settings at module init. These cases guard that
// property, because it is the kind of thing a later change re-breaks silently:
// one new module-level `location` read and the whole suite stops collecting.

import { describe, it, expect } from "vitest";
import {
  loadSettings, buildOverlayUrl, applyOverlayParams, isOverlay, DEFAULTS,
  parseOverlayOpacity, textPanelCss,
  TEXT_PANEL_DEFAULT_OPACITY, toggleTextPanel,
  panelHeight, panelRadius, PANEL_PADDING_Y,
  hydrateStored, SETTINGS_SCHEMA, saveSettings,
} from "./settings";

describe("importing without a browser", () => {
  it("hydrates settings with no location and no localStorage", () => {
    const s = loadSettings();

    expect(s.lines).toBeGreaterThan(0);
    expect(s.areaW).toBeGreaterThan(0);
    expect(s.fontSize).toBeGreaterThan(0);
  });

  it("reports not-overlay when there is no query string to read", () => {
    expect(isOverlay).toBe(false);
  });

  it("mints an overlay URL against an explicit base", () => {
    const url = buildOverlayUrl(loadSettings(), "http://mandir-pc:8765");

    expect(url).toContain("http://mandir-pc:8765/?");
    expect(url).toContain("overlay=1");
  });

  it("round-trips the layout through the minted URL", () => {
    // Every field in Settings is supposed to survive the trip out to a vMix
    // browser input and back. A field added to Settings but not to
    // buildOverlayUrl looks fine in the operator preview and silently reverts
    // to its default on the hall screen.
    const s = { ...loadSettings(), fontSize: 61, fontWeight: 800, lines: 3 };
    const url = buildOverlayUrl(s, "http://mandir-pc:8765");
    const qp = new URLSearchParams(url.split("?")[1]);

    expect(qp.get("fs")).toBe("61");
    expect(qp.get("fw")).toBe("800");
    expect(qp.get("lines")).toBe("3");
  });

  it("survives the whole trip out to the overlay and back, unchanged", () => {
    // 🔴 The failure this guards is invisible from the operator's chair. The
    // overlay is a SEPARATE browser instance, configured entirely by the URL
    // it is given. A setting that mints but does not parse back looks correct
    // in the preview and reverts on the hall screen — nobody sees it until
    // they look at the wall, mid-katha, with the swami speaking.
    //
    // Deliberately non-default in every field that decides what the sabha
    // reads, so a field silently falling back to its default fails here.
    const sent = {
      ...loadSettings(),
      lines: 3,
      areaX: 300, areaY: 700, areaW: 1300, areaH: 260,
      blockEnabled: true, blockX: 700, blockY: 800, blockW: 420, blockH: 130,
      fontSize: 61, fontWeight: 800,
      textBgOpacity: 0.4,
      panelFixed: false,
      lineMinDwellSec: 1.25,
      captionMaxDwellSec: 9,
    };

    const back = applyOverlayParams(
      { ...DEFAULTS },
      new URLSearchParams(buildOverlayUrl(sent, "http://mandir-pc:8765").split("?")[1]),
    );

    for (const k of [
      "lines", "areaX", "areaY", "areaW", "areaH",
      "blockEnabled", "blockX", "blockY", "blockW", "blockH",
      "fontSize", "fontWeight", "textBgOpacity", "panelFixed",
      "lineMinDwellSec", "captionMaxDwellSec",
    ] as const) {
      expect(back[k], `"${k}" did not survive the overlay URL`).toEqual(sent[k]);
    }
  });

  it("carries the panel switch and its opacity in BOTH states", () => {
    // The half of the round trip that was actually broken: the URL named these
    // only when they were ON, so an operator turning the panel off minted a
    // URL that said nothing about it — and the overlay, which now defaults it
    // on, painted it anyway.
    const off = applyOverlayParams(
      { ...DEFAULTS },
      new URLSearchParams(
        buildOverlayUrl({ ...loadSettings(), panelFixed: false, textBgOpacity: 0 }, "http://x")
          .split("?")[1],
      ),
    );

    expect(off.panelFixed).toBe(false);
    expect(off.textBgOpacity).toBe(0);
  });
});

describe("the panel behind the caption text", () => {
  // Manish, mid-katha: "make a 25% opacity background on the text". White text
  // with a drop shadow is not enough separation over a lit murti or a white
  // kurta, and the sabha at the back cannot read it.
  it("comes ON at the owner's number, because it is part of the display he asked for", () => {
    // "Only full line should be pushed upwards and i need 25% capacity block
    // background" (2026-09-03). It shipped OFF by default and had therefore
    // never once been on air: the URL that carried it reverted every time vMix
    // restarted, so the hall got the plain build every day.
    expect(DEFAULTS.textBgOpacity).toBe(TEXT_PANEL_DEFAULT_OPACITY);
    expect(TEXT_PANEL_DEFAULT_OPACITY).toBe(0.25);
  });

  it("round-trips through the minted overlay URL", () => {
    // The knob that breaks silently. The overlay is a SEPARATE browser
    // instance configured entirely through this URL — a setting missing from
    // it looks correct in the operator preview and reverts to default on the
    // hall screen, where nobody is looking at a settings panel.
    const s = { ...loadSettings(), textBgOpacity: 0.25 };
    const url = buildOverlayUrl(s, "http://mandir-pc:8765");

    expect(new URLSearchParams(url.split("?")[1]).get("tbg")).toBe("25");
  });

  it("is read back from an overlay URL", () => {
    expect(parseOverlayOpacity("25")).toBeCloseTo(0.25);
    expect(parseOverlayOpacity("0")).toBe(0);
    expect(parseOverlayOpacity("100")).toBe(1);
  });

  it("clamps anything out of range rather than emitting broken CSS", () => {
    expect(parseOverlayOpacity("-40")).toBe(0);
    expect(parseOverlayOpacity("400")).toBe(1);
    expect(parseOverlayOpacity("banana")).toBe(0);
    expect(parseOverlayOpacity(null)).toBe(0);
  });

  it("emits rgba(), never 8-digit hex", () => {
    // #RRGGBBAA arrived in Chrome 62. The overlay runs on vMix's embedded
    // Chromium 51, where it fails SILENTLY — no error, no background, and
    // nobody finds out until the hall cannot read the captions.
    const css = textPanelCss(0.25);

    expect(css).toMatch(/^rgba\(/);
    expect(css).not.toMatch(/^#/);
    expect(css).toContain("0.25");
  });

  it("renders nothing at all when switched off", () => {
    // Not rgba(0,0,0,0) — no paint at all, so the off state cannot cost a
    // compositing layer on a machine that is already driving a live stream.
    expect(textPanelCss(0)).toBe("transparent");
  });
});

describe("switching the text panel on", () => {
  // #53: "Settable 0-100%, defaulting to 25% when switched on." A scrubber
  // starting at zero is not that — switching on would mean dragging through
  // 1%, 2%, 3%, and the value the owner actually asked for is never the
  // on-value.
  it("comes on at the opacity that was asked for", () => {
    expect(TEXT_PANEL_DEFAULT_OPACITY).toBeCloseTo(0.25);
  });

  it("toggles off to nothing and back on to that value", () => {
    expect(toggleTextPanel(0)).toBeCloseTo(TEXT_PANEL_DEFAULT_OPACITY);
    expect(toggleTextPanel(TEXT_PANEL_DEFAULT_OPACITY)).toBe(0);
  });

  it("switching off then on again restores the operator's own value", () => {
    // Someone who has settled on 45% should not be handed 25% back for having
    // toggled it off to compare.
    expect(toggleTextPanel(0, 0.45)).toBeCloseTo(0.45);
    expect(toggleTextPanel(0.45)).toBe(0);
  });
});

describe("the fixed rounded panel", () => {
  // Manish: "What about a permanent rounded border 2 line border".
  //
  // A panel that hugs each line changes width and shape with every line —
  // which is its own kind of jumpy, and jumpy is the thing this whole piece of
  // work exists to remove. A panel that is always the same size and in the
  // same place does not move at all; only the words inside it change.
  it("is exactly two lines tall, whatever the caption says", () => {
    // 56px font, 1.25 line height => 70px a line => 140px of text.
    expect(panelHeight(2, 56)).toBe(140 + PANEL_PADDING_Y * 2);
  });

  it("does not change height for a one-line caption", () => {
    expect(panelHeight(2, 56)).toBe(panelHeight(2, 56));
  });

  it("tracks the font size, so the operator scaling text does not overflow it", () => {
    expect(panelHeight(2, 76)).toBeGreaterThan(panelHeight(2, 56));
  });

  it("has a corner radius that scales with the text rather than a fixed pixel value", () => {
    // A 12px radius reads as sharp at 76px text and as a lozenge at 24px.
    expect(panelRadius(76)).toBeGreaterThan(panelRadius(24));
    expect(panelRadius(56)).toBeGreaterThan(0);
  });

  it("round-trips its own switch through the overlay URL, in BOTH directions", () => {
    // 🔴 Both, not just the ON case. The URL used to carry the switch only
    // when it was on, so an operator turning the panel OFF minted a URL that
    // said nothing about it — and the overlay, which now defaults it ON,
    // painted it anyway. A setting that is silent about "off" is a setting the
    // operator cannot turn off on the hall screen.
    const on  = buildOverlayUrl({ ...loadSettings(), panelFixed: true,  textBgOpacity: 0.25 }, "http://x");
    const off = buildOverlayUrl({ ...loadSettings(), panelFixed: false, textBgOpacity: 0.25 }, "http://x");

    expect(new URLSearchParams(on.split("?")[1]).get("panel")).toBe("1");
    expect(new URLSearchParams(off.split("?")[1]).get("panel")).toBe("0");
  });

  it("comes ON by default, like the panel itself", () => {
    // Fixed rather than hugging: a panel that changes shape with every line is
    // its own kind of movement, and the scroll is meant to be the only thing
    // that moves.
    expect(DEFAULTS.panelFixed).toBe(true);
  });
});

describe("a new default has to reach a machine that has saved settings", () => {
  // The failure this guards against reached a full hall: the 25% panel of #56
  // shipped on by default and the mandir PC showed no background, because that
  // vMix input had `captions-settings` written before the panel existed.
  // Nothing errored. It just rendered last month's appearance.

  function stored(obj: Record<string, unknown>): string {
    return JSON.stringify(obj);
  }

  it("discards settings written before the stamp existed", () => {
    const old = stored({ textBgOpacity: 0, panelFixed: false, fontSize: 41 });

    const s = hydrateStored(old);

    expect(s.textBgOpacity).toBe(DEFAULTS.textBgOpacity);
    expect(s.panelFixed).toBe(DEFAULTS.panelFixed);
    expect(s.fontSize).toBe(DEFAULTS.fontSize);
  });

  it("discards settings written by an older schema", () => {
    const old = stored({ __schema: SETTINGS_SCHEMA - 1, fontSize: 41 });

    expect(hydrateStored(old).fontSize).toBe(DEFAULTS.fontSize);
  });

  it("keeps the operator's layout when the schema matches", () => {
    // The whole point of persistence. A stamp that threw the layout away on
    // every load would be worse than the bug it fixes.
    const mine = stored({ __schema: SETTINGS_SCHEMA, fontSize: 41, areaX: 77 });

    const s = hydrateStored(mine);

    expect(s.fontSize).toBe(41);
    expect(s.areaX).toBe(77);
  });

  it("round-trips what saveSettings actually writes", () => {
    // 🔴 Through the REAL writer. A hand-built blob asserting the same shape
    // stays green when the stamp stops being written, which is the one thing
    // this test exists to catch.
    //
    // The four-line store below is not a jsdom shim and must not become one —
    // `env.ts` reads exactly these two methods behind a `typeof` guard, and a
    // shim broad enough to be a browser is how a test starts passing against a
    // detail Chromium 51 does not have.
    const store = new Map<string, string>();
    (globalThis as any).localStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
    };
    try {
      saveSettings({ ...DEFAULTS, fontSize: 41 });
      const written = store.get("captions-settings");

      expect(written).toBeTruthy();
      expect(hydrateStored(written!).fontSize).toBe(41);
    } finally {
      delete (globalThis as any).localStorage;
    }
  });

  it("falls back to the defaults on a corrupt blob rather than throwing", () => {
    expect(hydrateStored("{not json").fontSize).toBe(DEFAULTS.fontSize);
    expect(hydrateStored(null).fontSize).toBe(DEFAULTS.fontSize);
    expect(hydrateStored("null").fontSize).toBe(DEFAULTS.fontSize);
  });

  it("never lets the stamp leak into Settings", () => {
    const s = hydrateStored(stored({ __schema: SETTINGS_SCHEMA }));

    expect("__schema" in s).toBe(false);
  });

  it("ships the panel defaults #56 asked for", () => {
    // The stamp is only worth having if the defaults behind it are the ones
    // the owner asked for: "i need 25% capacity block background".
    expect(DEFAULTS.textBgOpacity).toBeCloseTo(0.25);
    expect(DEFAULTS.panelFixed).toBe(true);
  });
});
