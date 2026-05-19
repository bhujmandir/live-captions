import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { ScrubNumber } from "@/components/ScrubNumber";

// ── Types ──────────────────────────────────────────────────────────

export type Bounds = { x: number; y: number; w: number; h: number };

export type SnapInfo = {
  axis:  "x" | "y";
  value: number;                  // the snap line's stage-coord position
  kind:  "third" | "edge" | "center" | "safe";
  label: string;                  // human chip label, e.g. "1/3", "centre"
};

type Props = {
  bounds:       Bounds;
  onChange:     (b: Bounds) => void;
  onCommit?:    () => void;
  selected:     boolean;
  onSelect:     () => void;
  stageBounds:  Bounds;           // 0,0,1920,1080
  containerBounds?: Bounds;       // when set, constrains drag inside this rect
  stageScale:   number;           // screen px per stage px
  color:        string;           // outline color
  label:        string;           // shown on the active rect's badge
  onSnap?:      (snaps: SnapInfo[]) => void;
  /** Snap targets for the X axis (stage units). */
  snapTargetsX?: SnapTarget[];
  snapTargetsY?: SnapTarget[];
};

type SnapTarget = { value: number; kind: SnapInfo["kind"]; label: string };

// ── Snap engine ─────────────────────────────────────────────────────
//
// Applies a 10px hard grid snap always, then a soft snap to any
// configured target within `softThresholdPx` (in screen px so the
// distance feels uniform regardless of zoom level — converted to
// stage units via stageScale at the call site).

const HARD_GRID = 10;

export function snapValue(v: number, targets: SnapTarget[], thresholdStage: number, bypass: boolean): { v: number; snap: SnapTarget | null } {
  if (bypass) return { v, snap: null };
  // Soft snap takes priority over grid — magnetic to thirds/edges/centre.
  let best: { snap: SnapTarget; dist: number } | null = null;
  for (const t of targets) {
    const d = Math.abs(v - t.value);
    if (d <= thresholdStage && (best === null || d < best.dist)) {
      best = { snap: t, dist: d };
    }
  }
  if (best) return { v: best.snap.value, snap: best.snap };
  // Otherwise fall back to the 10px grid.
  return { v: Math.round(v / HARD_GRID) * HARD_GRID, snap: null };
}

// Build the default snap target lists for a 1920×1080 stage. Operators
// can override per-component if needed, but the defaults cover all
// four checkboxes the operator picked: thirds, edges, centre, safe.
export function defaultSnapTargets(stage: Bounds): { x: SnapTarget[]; y: SnapTarget[] } {
  const xs: SnapTarget[] = [];
  const ys: SnapTarget[] = [];
  // Edges
  xs.push({ value: stage.x,             kind: "edge",   label: "left"   });
  xs.push({ value: stage.x + stage.w,   kind: "edge",   label: "right"  });
  ys.push({ value: stage.y,             kind: "edge",   label: "top"    });
  ys.push({ value: stage.y + stage.h,   kind: "edge",   label: "bottom" });
  // Centres
  xs.push({ value: stage.x + stage.w/2, kind: "center", label: "centre" });
  ys.push({ value: stage.y + stage.h/2, kind: "center", label: "centre" });
  // Thirds
  xs.push({ value: stage.x + stage.w/3,         kind: "third", label: "1/3" });
  xs.push({ value: stage.x + (2 * stage.w)/3,   kind: "third", label: "2/3" });
  ys.push({ value: stage.y + stage.h/3,         kind: "third", label: "1/3" });
  ys.push({ value: stage.y + (2 * stage.h)/3,   kind: "third", label: "2/3" });
  // Safe-area (10% inset)
  xs.push({ value: stage.x + stage.w * 0.1,         kind: "safe", label: "safe L" });
  xs.push({ value: stage.x + stage.w * 0.9,         kind: "safe", label: "safe R" });
  ys.push({ value: stage.y + stage.h * 0.1,         kind: "safe", label: "safe T" });
  ys.push({ value: stage.y + stage.h * 0.9,         kind: "safe", label: "safe B" });
  return { x: xs, y: ys };
}

// ── Component ───────────────────────────────────────────────────────

type DragMode = null | "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export function DraggableRect({
  bounds, onChange, onCommit,
  selected, onSelect,
  stageBounds, containerBounds, stageScale,
  color, label, onSnap,
  snapTargetsX, snapTargetsY,
}: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{
    mode: DragMode; startX: number; startY: number;
    startBounds: Bounds; pointerId: number; moved: boolean;
    shiftKey: boolean; altKey: boolean;
  }>({ mode: null, startX: 0, startY: 0, startBounds: bounds, pointerId: 0,
       moved: false, shiftKey: false, altKey: false });

  const [hovered, setHovered] = useState(false);

  // Build default snap targets if caller didn't pass any.
  const defaults = defaultSnapTargets(stageBounds);
  const tgtX = snapTargetsX ?? defaults.x;
  const tgtY = snapTargetsY ?? defaults.y;
  // Soft-snap threshold = 8 stage px (~5–10 screen px at typical zoom).
  const thresh = 8;

  const onPointerDown = (e: React.PointerEvent, mode: DragMode) => {
    if (e.button !== 0) return;
    onSelect();
    dragRef.current = {
      mode, startX: e.clientX, startY: e.clientY,
      startBounds: bounds, pointerId: e.pointerId, moved: false,
      shiftKey: e.shiftKey, altKey: e.altKey,
    };
    rootRef.current?.setPointerCapture(e.pointerId);
    e.stopPropagation();
    e.preventDefault();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const s = dragRef.current;
    if (!s.mode) return;
    s.shiftKey = e.shiftKey;
    s.altKey = e.altKey;
    const dx = (e.clientX - s.startX) / stageScale;
    const dy = (e.clientY - s.startY) / stageScale;
    if (Math.abs(dx) + Math.abs(dy) > 1) s.moved = true;

    // Compute candidate bounds for the active drag mode.
    let cand = computeCandidate(s.startBounds, s.mode, dx, dy, s.shiftKey);

    // Clamp to stage bounds.
    cand = clampInside(cand, stageBounds);
    // Further clamp to container bounds if set (reserved block inside
    // caption area).
    if (containerBounds) cand = clampInside(cand, containerBounds);

    // Snap. For move: snap left + right + centre-x of the rect against
    // X targets, plus top/bottom/centre-y against Y targets.
    // For resize: snap only the moving edge(s).
    const firedX: SnapInfo[] = [];
    const firedY: SnapInfo[] = [];
    if (s.mode === "move") {
      const cx = cand.x + cand.w / 2;
      const cy = cand.y + cand.h / 2;
      // Try snapping the rect's left edge, right edge, or centre to any
      // x target — pick the strongest fit (smallest delta).
      const candX = [
        { adjust: 0,            ref: cand.x          },
        { adjust: -cand.w,      ref: cand.x + cand.w },
        { adjust: -cand.w / 2,  ref: cx              },
      ];
      let bestX: { delta: number; snap: SnapTarget } | null = null;
      for (const { adjust, ref } of candX) {
        const { v, snap } = snapValue(ref, tgtX, thresh, s.altKey);
        if (snap) {
          const delta = v - ref;
          if (bestX === null || Math.abs(delta) < Math.abs(bestX.delta)) {
            bestX = { delta: delta, snap };
            cand.x = cand.x + delta;
            // adjust stored but not needed beyond delta
            void adjust;
          }
        }
      }
      // Fallback to 10px grid if no soft snap fired.
      if (!bestX && !s.altKey) cand.x = Math.round(cand.x / HARD_GRID) * HARD_GRID;
      if (bestX) firedX.push({ axis: "x", value: bestX.snap.value, kind: bestX.snap.kind, label: bestX.snap.label });

      const candY = [
        { adjust: 0,            ref: cand.y          },
        { adjust: -cand.h,      ref: cand.y + cand.h },
        { adjust: -cand.h / 2,  ref: cy              },
      ];
      let bestY: { delta: number; snap: SnapTarget } | null = null;
      for (const { ref } of candY) {
        const { v, snap } = snapValue(ref, tgtY, thresh, s.altKey);
        if (snap) {
          const delta = v - ref;
          if (bestY === null || Math.abs(delta) < Math.abs(bestY.delta)) {
            bestY = { delta: delta, snap };
            cand.y = cand.y + delta;
          }
        }
      }
      if (!bestY && !s.altKey) cand.y = Math.round(cand.y / HARD_GRID) * HARD_GRID;
      if (bestY) firedY.push({ axis: "y", value: bestY.snap.value, kind: bestY.snap.kind, label: bestY.snap.label });
    } else {
      // Resize: snap only the moving edges.
      const movesLeft   = s.mode.includes("w");
      const movesRight  = s.mode.includes("e");
      const movesTop    = s.mode.includes("n");
      const movesBottom = s.mode.includes("s");
      if (movesLeft) {
        const { v, snap } = snapValue(cand.x, tgtX, thresh, s.altKey);
        const nx = snap ? v : (s.altKey ? cand.x : Math.round(cand.x / HARD_GRID) * HARD_GRID);
        cand.w = cand.w + (cand.x - nx);
        cand.x = nx;
        if (snap) firedX.push({ axis: "x", value: snap.value, kind: snap.kind, label: snap.label });
      }
      if (movesRight) {
        const right = cand.x + cand.w;
        const { v, snap } = snapValue(right, tgtX, thresh, s.altKey);
        const nr = snap ? v : (s.altKey ? right : Math.round(right / HARD_GRID) * HARD_GRID);
        cand.w = nr - cand.x;
        if (snap) firedX.push({ axis: "x", value: snap.value, kind: snap.kind, label: snap.label });
      }
      if (movesTop) {
        const { v, snap } = snapValue(cand.y, tgtY, thresh, s.altKey);
        const ny = snap ? v : (s.altKey ? cand.y : Math.round(cand.y / HARD_GRID) * HARD_GRID);
        cand.h = cand.h + (cand.y - ny);
        cand.y = ny;
        if (snap) firedY.push({ axis: "y", value: snap.value, kind: snap.kind, label: snap.label });
      }
      if (movesBottom) {
        const bot = cand.y + cand.h;
        const { v, snap } = snapValue(bot, tgtY, thresh, s.altKey);
        const nb = snap ? v : (s.altKey ? bot : Math.round(bot / HARD_GRID) * HARD_GRID);
        cand.h = nb - cand.y;
        if (snap) firedY.push({ axis: "y", value: snap.value, kind: snap.kind, label: snap.label });
      }
    }

    // Floor minimums and re-clamp after snap/grid jitter.
    cand.w = Math.max(40, cand.w);
    cand.h = Math.max(20, cand.h);
    cand = clampInside(cand, stageBounds);
    if (containerBounds) cand = clampInside(cand, containerBounds);

    // Pixel-precise integers — float-mode snap math can leak tiny
    // residuals (319.9999…) into overlay URLs and localStorage.
    cand.x = Math.round(cand.x);
    cand.y = Math.round(cand.y);
    cand.w = Math.round(cand.w);
    cand.h = Math.round(cand.h);

    onSnap?.([...firedX, ...firedY]);
    onChange(cand);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const s = dragRef.current;
    if (!s.mode) return;
    try { rootRef.current?.releasePointerCapture(s.pointerId); } catch { /* ignore */ }
    dragRef.current = { ...s, mode: null };
    onSnap?.([]);
    if (s.moved) onCommit?.();
    e.preventDefault();
    e.stopPropagation();
  };

  // Visual flash on the rect when a snap engages — pulses for ~250ms.
  // Cheap implementation: animate the outline width via inline transition.
  // Handled by the CSS animation triggered when onSnap fires non-empty.

  // ── Render ─────────────────────────────────────────────────────
  const sx = bounds.x;
  const sy = bounds.y;
  const sw = bounds.w;
  const sh = bounds.h;

  const handle = (mode: Exclude<DragMode, null | "move">, top: number, left: number, cursor: string) => (
    <div
      key={mode}
      onPointerDown={(e) => onPointerDown(e, mode)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      style={{
        position: "absolute",
        top:  `${top}%`,
        left: `${left}%`,
        transform: "translate(-50%, -50%)",
        width: 10 / stageScale, height: 10 / stageScale,
        borderRadius: 2,
        background: color,
        border: `1px solid #fff`,
        cursor,
        boxShadow: "0 0 0 1px rgba(0,0,0,0.5)",
      }}
    />
  );

  return (
    <div
      ref={rootRef}
      onPointerDown={(e) => onPointerDown(e, "move")}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      onClick={(e) => { e.stopPropagation(); onSelect(); }}
      style={{
        position: "absolute",
        left: sx, top: sy,
        width: sw, height: sh,
        cursor: dragRef.current.mode ? "grabbing" : "grab",
        outline: selected
          ? `${2 / stageScale}px solid ${color}`
          : hovered
            ? `${1 / stageScale}px dashed ${color}80`
            : `${1 / stageScale}px dashed ${color}55`,
        outlineOffset: 0,
        background: selected ? `${color}11` : "transparent",
        pointerEvents: "auto",
        userSelect: "none",
        touchAction: "none",
      }}
    >
      {/* Numeric badges — visible always on selected rect, on hover otherwise */}
      {(selected || hovered) && (
        <>
          <Badge
            position="top-left" stageScale={stageScale} color={color}
            label={label}
            fields={[
              { label: "X", value: sx, onChange: (v) => onChange({ ...bounds, x: clampVal(v, stageBounds.x, stageBounds.x + stageBounds.w - sw) }), onCommit, min: stageBounds.x, max: stageBounds.x + stageBounds.w - sw },
              { label: "Y", value: sy, onChange: (v) => onChange({ ...bounds, y: clampVal(v, stageBounds.y, stageBounds.y + stageBounds.h - sh) }), onCommit, min: stageBounds.y, max: stageBounds.y + stageBounds.h - sh },
            ]}
          />
          <Badge
            position="bottom-right" stageScale={stageScale} color={color}
            fields={[
              { label: "W", value: sw, onChange: (v) => onChange({ ...bounds, w: clampVal(v, 40, stageBounds.x + stageBounds.w - sx) }), onCommit, min: 40, max: stageBounds.x + stageBounds.w - sx },
              { label: "H", value: sh, onChange: (v) => onChange({ ...bounds, h: clampVal(v, 20, stageBounds.y + stageBounds.h - sy) }), onCommit, min: 20, max: stageBounds.y + stageBounds.h - sy },
            ]}
          />
        </>
      )}

      {/* Resize handles — only visible when selected. Each handle stops
          pointer propagation so a click on the handle doesn't double-fire
          the body's "move" pointerdown. */}
      {selected && (
        <>
          {handle("nw",   0,   0, "nwse-resize")}
          {handle("n",    0,  50, "ns-resize")}
          {handle("ne",   0, 100, "nesw-resize")}
          {handle("e",   50, 100, "ew-resize")}
          {handle("se", 100, 100, "nwse-resize")}
          {handle("s",  100,  50, "ns-resize")}
          {handle("sw", 100,   0, "nesw-resize")}
          {handle("w",   50,   0, "ew-resize")}
        </>
      )}
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────

function computeCandidate(start: Bounds, mode: DragMode, dx: number, dy: number, shift: boolean): Bounds {
  const b = { ...start };
  switch (mode) {
    case "move": b.x += dx; b.y += dy; break;
    case "n": b.y += dy; b.h -= dy; break;
    case "s": b.h += dy; break;
    case "e": b.w += dx; break;
    case "w": b.x += dx; b.w -= dx; break;
    case "ne": b.y += dy; b.h -= dy; b.w += dx; break;
    case "nw": b.y += dy; b.h -= dy; b.x += dx; b.w -= dx; break;
    case "se": b.h += dy; b.w += dx; break;
    case "sw": b.h += dy; b.x += dx; b.w -= dx; break;
    default: break;
  }
  // Shift on a corner resize preserves aspect ratio. We pick whichever
  // axis the operator pushed further and lock the other axis to it.
  if (shift && mode && ["ne","nw","se","sw"].includes(mode)) {
    const aspect = start.w / start.h;
    if (Math.abs(b.w - start.w) > Math.abs(b.h - start.h) * aspect) {
      const nh = b.w / aspect;
      const dh = nh - b.h;
      if (mode === "nw" || mode === "ne") b.y -= dh;
      b.h = nh;
    } else {
      const nw = b.h * aspect;
      const dw = nw - b.w;
      if (mode === "nw" || mode === "sw") b.x -= dw;
      b.w = nw;
    }
  }
  return b;
}

function clampInside(b: Bounds, container: Bounds): Bounds {
  const out = { ...b };
  if (out.w > container.w) out.w = container.w;
  if (out.h > container.h) out.h = container.h;
  if (out.x < container.x) out.x = container.x;
  if (out.y < container.y) out.y = container.y;
  if (out.x + out.w > container.x + container.w) out.x = container.x + container.w - out.w;
  if (out.y + out.h > container.y + container.h) out.y = container.y + container.h - out.h;
  return out;
}

function clampVal(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

// ── Badge ───────────────────────────────────────────────────────────

function Badge({
  position, stageScale, color, label, fields,
}: {
  position: "top-left" | "bottom-right";
  stageScale: number;
  color: string;
  label?: string;
  fields: Array<{
    label: string; value: number;
    onChange: (v: number) => void; onCommit?: () => void;
    min?: number; max?: number;
  }>;
}) {
  // Badges live OUTSIDE the rect so they're not clipped by the
  // operator-only outline and so they don't capture pointer events
  // meant for the rect body.
  const offset = 6 / stageScale;
  const style: React.CSSProperties = {
    position: "absolute",
    pointerEvents: "auto",
    background: "rgba(0,0,0,0.85)",
    color: "#fff",
    fontFamily: "JetBrains Mono, monospace",
    fontSize: 11 / stageScale,
    padding: `${2 / stageScale}px ${5 / stageScale}px`,
    borderRadius: 3 / stageScale,
    border: `${1 / stageScale}px solid ${color}`,
    whiteSpace: "nowrap",
    display: "flex",
    gap: 4 / stageScale,
    alignItems: "center",
  };
  if (position === "top-left") {
    style.left = 0; style.top = -offset; style.transform = "translateY(-100%)";
  } else {
    style.right = 0; style.bottom = -offset; style.transform = "translateY(100%)";
  }
  return (
    <div
      style={style}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      {label && position === "top-left" && (
        <span style={{ color: color, fontWeight: 600, marginRight: 4 / stageScale }}>
          {label.toUpperCase()}
        </span>
      )}
      {fields.map((f, i) => (
        <span key={f.label} style={{ display: "inline-flex", alignItems: "center", gap: 2 / stageScale }}>
          <span style={{ opacity: 0.55 }}>{f.label}</span>
          <ScrubNumber
            value={f.value}
            onChange={f.onChange}
            onCommit={f.onCommit}
            min={f.min}
            max={f.max}
            width={36 / stageScale}
            ariaLabel={f.label}
          />
          {i < fields.length - 1 && <span style={{ opacity: 0.3 }}>·</span>}
        </span>
      ))}
    </div>
  );
}
