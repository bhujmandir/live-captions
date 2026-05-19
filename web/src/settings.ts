// Caption-display settings. Persisted to localStorage under
// `captions-settings`; URL params (overlay mode) override on load.
// If you add a field, mirror it in buildOverlayUrl below so it round-trips
// through the minted PP Web Object URL.

export type Settings = {
  // Visible-text trim — how many wrapped lines to keep on screen.
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
};

// Layout presets — applied via the "preset" dropdown in the operator
// settings drawer. Customising any field flips the preset to "custom".
export const PRESETS: Record<"small" | "medium" | "large", Partial<Settings>> = {
  small:  { areaX: 360, areaY: 880, areaW: 1200, areaH: 160, fontSize: 36, fontWeight: 500, lines: 2 },
  medium: { areaX: 320, areaY: 780, areaW: 1280, areaH: 240, fontSize: 56, fontWeight: 500, lines: 3 },
  large:  { areaX: 280, areaY: 680, areaW: 1360, areaH: 340, fontSize: 76, fontWeight: 600, lines: 3 },
};

export const DEFAULTS: Settings = {
  lines: 3,
  areaX: 320, areaY: 780, areaW: 1280, areaH: 240,
  blockEnabled: true, blockX: 760, blockY: 860, blockW: 400, blockH: 120,
  fontSize: 56, fontWeight: 500,
  fontFamily: "system",
  bg: "transparent",
  source: "gu-IN", target: "en-IN",
  layoutPreset: "medium",
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

function clampInt(v: string | null, lo: number, hi: number, fallback: number): number {
  if (v == null) return fallback;
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(lo, Math.min(hi, n));
}

// Hydrate settings. Order of precedence: defaults → localStorage → URL
// params (URL wins so overlay URLs from PP override everything).
export function loadSettings(): Settings {
  const s: Settings = { ...DEFAULTS };
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    for (const k of Object.keys(s) as (keyof Settings)[]) {
      if (k in stored) (s as any)[k] = stored[k];
    }
  } catch { /* ignore */ }

  const qp = new URLSearchParams(location.search);
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
  return s;
}

export function saveSettings(s: Settings): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch { /* ignore */ }
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
  const origin = base ?? location.origin;
  return `${origin}/?${p.toString()}`;
}

// Detected once on module load — overlay strips chrome and runs the
// rendering path 1:1 at viewport size.
export const isOverlay = new URLSearchParams(location.search).get("overlay") === "1";

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
