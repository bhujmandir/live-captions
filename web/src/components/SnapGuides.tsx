import { useEffect, useRef, useState } from "react";
import type { SnapInfo } from "@/components/DraggableRect";

// Renders the visual snap feedback layered ON TOP of the stage.
// Three signals (per the user's pick):
//   • Faint orange dashed line across the stage at the snap target
//   • Small chip naming the snap (e.g. "1/3", "centre", "safe L")
//   • Brief outline pulse on the active rect (handled by the rect
//     itself using a CSS animation — this component just emits the
//     CSS class via a key change)
//
// Stage coords throughout; placed inside the same transformed stage so
// the dashed lines align with the rectangle edges visually.

type Props = {
  snaps:      SnapInfo[];
  stageScale: number;
  stageW:     number;
  stageH:     number;
};

export function SnapGuides({ snaps, stageScale, stageW, stageH }: Props) {
  // We let the dashed lines linger ~200ms after a snap stops firing so
  // they don't flicker on every micro-update. Keep state of recent
  // snaps in a small queue keyed by axis+value.
  const [lingering, setLingering] = useState<SnapInfo[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    // Merge incoming snaps into the lingering set.
    setLingering((prev) => {
      const map = new Map<string, SnapInfo>();
      prev.forEach((s) => map.set(snapKey(s), s));
      snaps.forEach((s) => map.set(snapKey(s), s));
      return Array.from(map.values());
    });

    // For each currently-active snap, clear any pending removal timer.
    // For each snap not in the active set, set a fade-out timer.
    const activeKeys = new Set(snaps.map(snapKey));
    for (const [k, t] of timersRef.current) {
      if (activeKeys.has(k)) {
        clearTimeout(t);
        timersRef.current.delete(k);
      }
    }
    lingering.forEach((s) => {
      const k = snapKey(s);
      if (!activeKeys.has(k) && !timersRef.current.has(k)) {
        const t = setTimeout(() => {
          setLingering((prev) => prev.filter((x) => snapKey(x) !== k));
          timersRef.current.delete(k);
        }, 200);
        timersRef.current.set(k, t);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snaps]);

  return (
    <div style={{
      position: "absolute",
      inset: 0,
      pointerEvents: "none",
    }}>
      {lingering.map((s) => {
        const isX = s.axis === "x";
        const style: React.CSSProperties = isX ? {
          position: "absolute",
          top: 0, bottom: 0,
          left: s.value,
          width: 1 / stageScale,
          borderLeft: `${1 / stageScale}px dashed ${colorFor(s.kind)}`,
        } : {
          position: "absolute",
          left: 0, right: 0,
          top: s.value,
          height: 1 / stageScale,
          borderTop: `${1 / stageScale}px dashed ${colorFor(s.kind)}`,
        };
        return <div key={snapKey(s)} style={style} />;
      })}
      {/* Chip at the most-recent snap target, near the centre of the stage. */}
      {snaps.length > 0 && (
        <div style={{
          position: "absolute",
          left: stageW / 2,
          top:  stageH / 2,
          transform: "translate(-50%, -50%)",
          background: "rgba(0,0,0,0.85)",
          color: "#ffe9c4",
          padding: `${4 / stageScale}px ${8 / stageScale}px`,
          borderRadius: 4 / stageScale,
          fontFamily: "JetBrains Mono, monospace",
          fontSize: 11 / stageScale,
          border: `${1 / stageScale}px solid rgba(255,140,0,0.7)`,
          whiteSpace: "nowrap",
        }}>
          snap · {snaps.map((s) => s.label).join(" + ")}
        </div>
      )}
    </div>
  );
}

function snapKey(s: SnapInfo) { return `${s.axis}:${s.kind}:${s.value}`; }
function colorFor(k: SnapInfo["kind"]): string {
  switch (k) {
    case "edge":   return "rgba(255,140,0,0.6)";
    case "center": return "rgba(255,140,0,0.7)";
    case "third":  return "rgba(120,180,255,0.55)";
    case "safe":   return "rgba(76,175,80,0.55)";
  }
}
