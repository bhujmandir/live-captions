// Caption-display settings. Persisted to localStorage under
// `captions-settings`; URL params (overlay mode) override on load.
// If you add a field, mirror it in buildOverlayUrl below so it round-trips
// through the minted PP Web Object URL.

import { params, origin, readStored, writeStored } from "./env";

export type Settings = {
  // How many lines the hall reads at once — the height of the rolling scroll.
  // A PREFERENCE: the caption box's own height can hold fewer, and it wins.
  lines: number;
  // Caption area — a sub-rectangle of the 1920×1080 stage where the text
  // renders. Size your ProPresenter Web Object to match this rectangle.
  areaX: number; areaY: number; areaW: number; areaH: number;
  // Optional reserved-zone block inside the area that text wraps around
  // (two CSS shape-outside floats). Useful when the LED wall has a
  // centred logo / lower-third.
  blockEnabled: boolean;
  blockX: number; blockY: number; blockW: number; blockH: number;
  // Font + background.
  fontSize: number; fontWeight: number;
  fontFamily: string;               // matches a CAPTION_FONTS id below
  bg: string;                       // "transparent" or a CSS color
  // Language pair (Sarvam Saaras source/target). The server derives the
  // pipeline (translate vs transcribe + Mayura) from these two.
  source: string; target: string;
  // Layout preset name. Switching to "custom" lets the operator type
  // raw numbers; the named presets snap to a sensible default.
  layoutPreset: "small" | "medium" | "large" | "custom";
  // Sarvam connect parameters (model + VAD only — mode and language_code
  // are derived server-side from source/target).
  sarvamModel: string;
  sarvamHighVad: boolean;           // high_vad_sensitivity
  sarvamVadSignals: boolean;        // vad_signals (drives partial …)
  // Client-side audio gate (pre-filter before upload, saves Sarvam credits).
  silencePct: number;               // peak threshold, % of int16 full-scale
  hangoverSec: number;              // keep sending this long after last loud chunk
  // Overlay: when true, the minted URL carries `fit=width` so the overlay
  // forces AREA-mode width-fit regardless of viewport aspect. Useful when
  // the overlay is opened in a regular browser window (not a tightly-
  // sized PP Web Object) and would otherwise letterbox.
  overlayFitWidth: boolean;
  // Caption timing. Both live here rather than as module constants because
  // the overlay is a SEPARATE browser instance from the operator UI — it is
  // configured entirely through the minted URL, so a timing knob that is not
  // a setting cannot be changed on the day without editing code.
  //
  // How long the newest line stays put before the next may scroll on.
  //
  // ⚠️ A LINE, not a block. One sentence is commonly two lines, so this floor
  // has to fit under the per-line arrival interval — about half the caption
  // interval — or the screen falls behind by the difference on every sentence.
  lineMinDwellSec: number;
  // Draw the panel as ONE fixed rounded rectangle instead of hugging each
  // rendered line.
  //
  // A panel that hugs the text changes width and shape with every line, which
  // is its own kind of movement — and the scroll is meant to be the ONLY thing
  // that moves. A fixed panel never moves; only the words inside it do.
  panelFixed: boolean;
  // Opacity of the translucent panel behind the caption text, 0..1.
  //
  // 0 is OFF and means no paint at all. White text with a drop shadow is not
  // enough separation over a bright shot — a lit murti, a white kurta — and the
  // sabha at the back cannot read it. This is the standard broadcast answer.
  textBgOpacity: number;
  // How long after the last caption the screen is taken down.
  //
  // ONE setting, not two. The maximum a line may sit there and the silence
  // clear are the same question — "how long before this stops reading as
  // current and starts reading as stuck" — and two knobs for one question can
  // disagree, which is how the caption area and the line count ended up
  // clipping text at each other.
  captionMaxDwellSec: number;
};

// Layout presets — applied via the "preset" dropdown in the operator
// settings drawer. Customising any field flips the preset to "custom".
//
// Every preset is TWO lines. The owner settled the shape on Day 3: "2 on the
// screen and 3rd 1 is on the queue and 4th one is being constructed". Two
// lines is the shape, not a ceiling that varies by preset — the presets differ
// in where the captions sit and how big they are, not in how much text the
// sabha is asked to take in at once.
//
// The overlay CUTS arriving text into single lines and scrolls them on one at
// a time, so the server's CAPTION_SENTENCE_MAX_CHARS is not a per-line ceiling
// and does not have to agree with this number. (It once was justified that
// way, on a "~90 chars/line" figure taken from the mandir PC's own layout; the
// shipped layout holds about 51. See live_captions.py.)
export const PRESETS: Record<"small" | "medium" | "large", Partial<Settings>> = {
  small:  { areaX: 360, areaY: 880, areaW: 1200, areaH: 160, fontSize: 36, fontWeight: 500, lines: 2 },
  medium: { areaX: 320, areaY: 780, areaW: 1280, areaH: 240, fontSize: 56, fontWeight: 500, lines: 2 },
  large:  { areaX: 280, areaY: 680, areaW: 1360, areaH: 340, fontSize: 76, fontWeight: 600, lines: 2 },
};

// 🔴 Declared BEFORE `DEFAULTS`, which reads it. Settings are hydrated at
// module load, and a `const` referenced before its declaration throws at
// import — which would take the overlay down with it.
/**
 * Opacity the panel comes on at.
 *
 * The owner's own number: "make a 25% opacity background on the text".
 */
export const TEXT_PANEL_DEFAULT_OPACITY = 0.25;

export const DEFAULTS: Settings = {
  lines: 2,
  areaX: 320, areaY: 780, areaW: 1280, areaH: 240,
  blockEnabled: true, blockX: 760, blockY: 860, blockW: 400, blockH: 120,
  fontSize: 56, fontWeight: 500,
  fontFamily: "system",
  bg: "transparent",
  source: "gu-IN", target: "en-IN",
  layoutPreset: "medium",
  // ON by default, at the owner's number: "Only full line should be pushed
  // upwards and i need 25% capacity block background" (2026-09-03). The panel
  // is part of the display he asked for, not an extra somebody switches on —
  // and it had never once been on air, because the URL that carried it reverted
  // every time vMix restarted.
  textBgOpacity: TEXT_PANEL_DEFAULT_OPACITY,
  panelFixed: true,
  // 1.5s is the broadcast minimum for one line, and it sits under the ~1.9s
  // per-line interval measured on Day 3. The old 2.0s was a two-line BLOCK's
  // dwell; keeping it per line would make a two-line sentence take 4.0s to
  // display against a 3.83s cadence, and the screen would slide behind.
  lineMinDwellSec: 1.5,
  // 12s is past any ordinary pause for breath and well short of the gap
  // between passages. Long by broadcast norms (~6s for two lines), which is
  // a hall-facing judgement rather than a measured value: a caption wiped
  // mid-read cannot be recovered, one held too long merely looks stale.
  captionMaxDwellSec: 12,
  sarvamModel: "saaras:v3",
  sarvamHighVad: true,
  sarvamVadSignals: true,
  silencePct: 1.0,
  hangoverSec: 1.5,
  overlayFitWidth: false,
};

// Curated list of caption-suitable fonts. All entries use system /
// already-installed fonts so there's no FOUT / external load — the
// font is available the moment the page parses. Stacks include
// fallbacks for Indic glyphs (the system falls back automatically
// when the primary font lacks coverage).
export const CAPTION_FONTS: Array<{ id: string; name: string; stack: string; note?: string }> = [
  { id: "system",    name: "System (recommended)",
    stack: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    note: "Apple's San Francisco on macOS — clean, neutral, excellent for captions." },
  { id: "helvetica", name: "Helvetica / Arial",
    stack: "Helvetica, Arial, sans-serif",
    note: "Familiar neutral sans-serif. Reliable cross-platform." },
  { id: "avenir",    name: "Avenir Next",
    stack: "'Avenir Next', Avenir, sans-serif",
    note: "Geometric sans, modern feel. Mac default." },
  { id: "verdana",   name: "Verdana",
    stack: "Verdana, Geneva, sans-serif",
    note: "Wider proportions — excellent at small sizes on imperfect projections." },
  { id: "tahoma",    name: "Tahoma",
    stack: "Tahoma, Geneva, sans-serif",
    note: "Similar to Verdana, slightly tighter." },
  { id: "trebuchet", name: "Trebuchet MS",
    stack: "'Trebuchet MS', sans-serif",
    note: "Friendly humanist sans. Good visual rhythm." },
  { id: "georgia",   name: "Georgia (serif)",
    stack: "Georgia, 'Times New Roman', serif",
    note: "Serif option — calmer, traditional. Useful for liturgical content." },
  { id: "mono",      name: "Mono (JetBrains/SF Mono)",
    stack: "'JetBrains Mono', 'SF Mono', Menlo, monospace",
    note: "Fixed-width. Disambiguates similar glyphs." },
];

export function fontStackFor(id: string): string {
  return (CAPTION_FONTS.find((f) => f.id === id) || CAPTION_FONTS[0]).stack;
}

const STORAGE_KEY = "captions-settings";

/**
 * Schema of what `captions-settings` holds. Bump ONLY when a default changes
 * in a way that has to reach machines which have already saved settings.
 *
 * 🔴 Why this exists: stored settings used to beat the defaults
 * unconditionally, so every new default shipped dead on arrival to every
 * machine that had ever saved — which is every machine that matters — and it
 * failed silently. The 25% panel of #56 reached the mandir PC as a bundle that
 * had it on by default and a hall screen with no background behind the words,
 * no error anywhere, because a `captions-settings` written before the panel
 * existed still held `textBgOpacity: 0` and `panelFixed: false`. A reload does
 * not help; localStorage outlives it.
 *
 * A bump costs the operator their saved layout, so it is not free and it is
 * not automatic. Changing a default nobody has overridden does not need one.
 */
export const SETTINGS_SCHEMA = 2;

/** Where the stamp lives. Not a `Settings` field, so it never round-trips. */
const SCHEMA_FIELD = "__schema";

/**
 * Fold a stored settings blob onto the defaults, discarding anything an older
 * bundle wrote.
 *
 * Pure and exported for the same reason `applyOverlayParams` is: the failure
 * this guards against is invisible from inside a browser — everything renders,
 * it just renders last month's appearance — so it has to be assertable off it.
 */
export function hydrateStored(raw: string | null): Settings {
  const s: Settings = { ...DEFAULTS };
  try {
    const stored = JSON.parse(raw || "{}");
    // A blob with no stamp is pre-versioning, which is exactly the case that
    // caused this. Anything but the current schema is discarded whole: a
    // partial merge would leave the operator half on old defaults and half on
    // new, which is harder to diagnose than either.
    if (!stored || stored[SCHEMA_FIELD] !== SETTINGS_SCHEMA) return s;
    for (const k of Object.keys(s) as (keyof Settings)[]) {
      if (k in stored) (s as any)[k] = stored[k];
    }
  } catch { /* ignore — a corrupt blob is the defaults, never a crash */ }
  return s;
}

function clampInt(v: string | null, lo: number, hi: number, fallback: number): number {
  if (v == null) return fallback;
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

// Fractional-seconds variant. The timing knobs are worth setting to 1.5 or
// 2.5, so rounding them to whole seconds would throw away the resolution the
// decision actually needs.
function clampNum(v: string | null, lo: number, hi: number, fallback: number): number {
  if (v == null) return fallback;
  const n = parseFloat(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

// Hydrate settings. Order of precedence: defaults → localStorage → URL
// params (URL wins so overlay URLs from PP override everything).
export function loadSettings(): Settings {
  const s = hydrateStored(readStored(STORAGE_KEY));

  return applyOverlayParams(s, params());
}

/**
 * Apply an overlay URL's query string on top of settings.
 *
 * 🔴 Exported and pure so the ROUND TRIP can be asserted. The overlay is a
 * separate browser instance configured entirely by its minted URL, so a
 * setting that mints but does not parse back — or parses only in one of its
 * two states — looks perfectly correct in the operator preview and silently
 * reverts on the hall screen. That has already happened twice here, and both
 * times it was invisible until somebody looked at the wall.
 *
 * While this lived inside `loadSettings`, reading the module-level query
 * string, nothing could test it against a URL of its own.
 */
export function applyOverlayParams(base: Settings, qp: URLSearchParams): Settings {
  const s: Settings = { ...base };
  if (qp.has("lines"))  s.lines = clampInt(qp.get("lines"), 1, 10, s.lines);
  if (qp.has("area")) {
    const p = (qp.get("area") || "").split(",").map(Number);
    if (p.length === 4 && p.every(Number.isFinite))
      [s.areaX, s.areaY, s.areaW, s.areaH] = p;
  }
  if (qp.has("block")) {
    const p = (qp.get("block") || "").split(",").map(Number);
    if (p.length === 4 && p.every(Number.isFinite)) {
      s.blockEnabled = true;
      [s.blockX, s.blockY, s.blockW, s.blockH] = p;
    } else if (p.length === 2 && p.every(Number.isFinite)) {
      // Legacy 2-tuple form (y, h).
      s.blockEnabled = true;
      [s.blockY, s.blockH] = p;
    }
  } else if (qp.has("noblock")) {
    s.blockEnabled = false;
  }
  if (qp.has("fs")) s.fontSize   = clampInt(qp.get("fs"), 8, 200, s.fontSize);
  if (qp.has("fw")) s.fontWeight = clampInt(qp.get("fw"), 100, 900, s.fontWeight);
  if (qp.has("bg")) s.bg = qp.get("bg") || s.bg;
  if (qp.has("ff")) {
    const id = qp.get("ff") || "";
    if (CAPTION_FONTS.some((f) => f.id === id)) s.fontFamily = id;
  }
  if (qp.get("fit") === "width") s.overlayFitWidth = true;
  if (qp.has("tbg")) s.textBgOpacity = parseOverlayOpacity(qp.get("tbg"));
  if (qp.has("panel")) s.panelFixed = qp.get("panel") === "1";
  if (qp.has("dwell"))    s.lineMinDwellSec    = clampNum(qp.get("dwell"), 0.25, 10, s.lineMinDwellSec);
  // `clearafter` is the older name for the same knob and is still honoured —
  // overlay URLs minted before this change are pasted into vMix and PP and
  // are not re-minted just because the code moved on.
  if (qp.has("maxdwell"))   s.captionMaxDwellSec = clampNum(qp.get("maxdwell"), 1, 120, s.captionMaxDwellSec);
  else if (qp.has("clearafter")) s.captionMaxDwellSec = clampNum(qp.get("clearafter"), 1, 120, s.captionMaxDwellSec);
  return s;
}

export function saveSettings(s: Settings): void {
  writeStored(STORAGE_KEY, JSON.stringify({ ...s, [SCHEMA_FIELD]: SETTINGS_SCHEMA }));
}

// Mint an overlay URL for ProPresenter's Web Object. Returns an absolute
// URL with the active layout baked into query params, so the rendered
// overlay matches what the operator is seeing in the preview without
// having to re-configure anything in PP.
export function buildOverlayUrl(s: Settings, base?: string): string {
  const p = new URLSearchParams();
  p.set("overlay", "1");
  p.set("lines", String(s.lines));
  p.set("area", `${s.areaX},${s.areaY},${s.areaW},${s.areaH}`);
  if (s.blockEnabled) p.set("block", `${s.blockX},${s.blockY},${s.blockW},${s.blockH}`);
  else                p.set("noblock", "1");
  p.set("fs", String(s.fontSize));
  p.set("fw", String(s.fontWeight));
  if (s.bg && s.bg !== "transparent") p.set("bg", s.bg);
  if (s.fontFamily && s.fontFamily !== DEFAULTS.fontFamily) p.set("ff", s.fontFamily);
  if (s.overlayFitWidth) p.set("fit", "width");
  // 🔴 ALWAYS emitted, both of them, including the off state.
  //
  // These used to be written only when switched ON, which was safe while the
  // defaults were off and is a fault now the panel is on by default: an
  // operator who turned it off would mint a URL that said nothing about it,
  // and the hall screen would read its own default and paint the panel anyway.
  // A setting that only round-trips in one direction is not a setting.
  p.set("tbg", String(Math.round(s.textBgOpacity * 100)));
  p.set("panel", s.panelFixed ? "1" : "0");
  p.set("dwell", String(s.lineMinDwellSec));
  p.set("maxdwell", String(s.captionMaxDwellSec));
  const root = base ?? origin();
  return `${root}/?${p.toString()}`;
}

// Detected once on module load — overlay strips chrome and runs the
// rendering path 1:1 at viewport size.
export const isOverlay = params().get("overlay") === "1";

// Layout preset detection. If the current geometry matches one of the
// named presets, the dropdown shows that preset; otherwise "custom".
export function inferLayoutPreset(s: Settings): "small" | "medium" | "large" | "custom" {
  for (const [name, p] of Object.entries(PRESETS) as Array<["small"|"medium"|"large", Partial<Settings>]>) {
    if (
      p.areaX === s.areaX && p.areaY === s.areaY &&
      p.areaW === s.areaW && p.areaH === s.areaH &&
      p.fontSize === s.fontSize && p.fontWeight === s.fontWeight &&
      p.lines === s.lines
    ) return name;
  }
  return "custom";
}

/**
 * Read the `tbg` overlay parameter — a percentage — as an opacity 0..1.
 *
 * Anything unparseable or out of range clamps rather than propagating. A NaN
 * reaching the style attribute produces CSS the browser drops silently, and a
 * silently dropped caption panel is indistinguishable from one that was never
 * asked for.
 */
export function parseOverlayOpacity(value: string | null): number {
  if (value == null) return 0;
  const pct = parseFloat(value);
  if (!Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(1, pct / 100));
}

/**
 * The CSS background value for the caption text panel.
 *
 * 🔴 `rgba()`, never `#RRGGBBAA`. Eight-digit hex arrived in Chrome 62 and the
 * overlay runs on vMix's embedded **Chromium 51**, where it fails SILENTLY — no
 * error, no background, and nobody finds out until the hall cannot read the
 * captions. The same class of assumption already cost this project a live
 * fault.
 *
 * Black, not a palette: the panel exists to create contrast under white text,
 * and offering a colour choice invites one that reduces it. Opacity is the knob.
 *
 * Off returns `transparent` rather than `rgba(0,0,0,0)` so the off state costs
 * no paint at all on a machine already driving a live stream.
 */
export function textPanelCss(opacity: number): string {
  const a = Number.isFinite(opacity) ? Math.max(0, Math.min(1, opacity)) : 0;
  if (a <= 0) return "transparent";
  return `rgba(0, 0, 0, ${a})`;
}

/**
 * Toggle the panel, remembering where it was.
 *
 * Switching off returns 0. Switching on returns `remembered` if the operator
 * had settled on a value, otherwise the asked-for default — so toggling off to
 * compare and back on again does not silently reset their choice.
 */
export function toggleTextPanel(current: number, remembered = 0): number {
  if (current > 0) return 0;
  return remembered > 0 ? remembered : TEXT_PANEL_DEFAULT_OPACITY;
}

// Breathing room inside the fixed panel, above and below the text.
//
// Text touching the edge of a box reads as cramped and, on a projected screen
// at the back of a hall, as clipped. Half a line of leading each side is the
// usual broadcast proportion.
export const PANEL_PADDING_Y = 18;
export const PANEL_PADDING_X = 28;

/**
 * Height of the fixed panel: always `lines` of text, plus padding.
 *
 * Fixed is the point. It does not shrink for a one-line caption, because a
 * panel that resizes as lines change is exactly the movement this replaces.
 */
export function panelHeight(lines: number, fontSize: number, lineHeight = 1.25): number {
  const l = Math.max(1, Math.floor(lines));
  const box = Math.max(0, fontSize) * lineHeight;
  return Math.round(l * box) + PANEL_PADDING_Y * 2;
}

/**
 * Corner radius, as a proportion of the text size rather than a fixed pixel
 * value.
 *
 * The operator can scale the captions from 8px to 200px. A 12px radius reads
 * as square at the top of that range and as a lozenge at the bottom; tying it
 * to the font keeps the same shape at every size.
 */
export function panelRadius(fontSize: number): number {
  return Math.max(4, Math.round(Math.max(0, fontSize) * 0.28));
}
