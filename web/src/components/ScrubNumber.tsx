import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * Figma-style number control. Two interaction modes:
 *
 *  • Pointer-down + horizontal drag  → scrub value live (no edit input)
 *  • Pointer-down + release (no drag) → enter edit mode, text selected
 *
 * Edit mode: native <input>. Enter / blur commits; Esc cancels (reverts
 * to the pre-edit value).
 *
 * Drag end commits via onCommit() so the operator's undo stack can
 * snapshot the value. Intermediate drag values fire onChange() so the
 * preview can update live.
 */

type Props = {
  value:    number;
  onChange: (v: number) => void;          // live, every frame during scrub or every keypress in edit
  onCommit?: () => void;                  // fires once when the scrub/edit finishes
  min?:    number;
  max?:    number;
  step?:   number;                        // increment per scrubbed pixel (default 1)
  precision?: number;                     // decimal places shown (default 0)
  width?:  number;                        // px; defaults to 3ch-ish auto
  className?: string;
  ariaLabel?: string;
};

export function ScrubNumber({
  value, onChange, onCommit,
  min = -Infinity, max = Infinity,
  step = 1, precision = 0,
  width, className, ariaLabel,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft,   setDraft]   = useState<string>("");
  const elRef    = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Drag state — held in refs so we don't re-render on every pointer move.
  const dragRef = useRef({
    active:    false,
    startX:    0,
    startVal:  0,
    moved:     false,
    pointerId: 0,
  });

  // Auto-focus + select on entering edit mode.
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const clamp = (v: number) => Math.max(min, Math.min(max, v));

  const onPointerDown = (e: React.PointerEvent) => {
    if (editing) return;
    if (e.button !== 0) return;
    dragRef.current = {
      active:    true,
      startX:    e.clientX,
      startVal:  value,
      moved:     false,
      pointerId: e.pointerId,
    };
    elRef.current?.setPointerCapture(e.pointerId);
    e.preventDefault();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    // 1px of pointer movement = `step` units. Shift slows things down 4×
    // for fine adjustments; mirrors most design tools.
    const factor = e.shiftKey ? step / 4 : step;
    if (Math.abs(dx) > 2) dragRef.current.moved = true;
    if (!dragRef.current.moved) return;
    const next = clamp(dragRef.current.startVal + dx * factor);
    onChange(round(next, precision));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragRef.current.active) return;
    const moved = dragRef.current.moved;
    dragRef.current.active = false;
    try { elRef.current?.releasePointerCapture(dragRef.current.pointerId); } catch { /* ignore */ }
    if (moved) {
      // Scrub ended — commit so undo can snapshot.
      onCommit?.();
    } else {
      // Click without movement → enter edit mode.
      setDraft(round(value, precision).toString());
      setEditing(true);
    }
    e.preventDefault();
  };

  const onEditCommit = () => {
    const n = parseFloat(draft);
    if (Number.isFinite(n)) {
      onChange(clamp(round(n, precision)));
      onCommit?.();
    }
    setEditing(false);
  };

  const onEditCancel = () => setEditing(false);

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={onEditCommit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); onEditCommit(); }
          else if (e.key === "Escape") { e.preventDefault(); onEditCancel(); }
        }}
        aria-label={ariaLabel}
        style={width ? { width } : undefined}
        className={cn(
          "bg-bg text-fg border border-accent rounded px-1 text-center",
          "font-mono text-[11px] outline-none",
          className,
        )}
      />
    );
  }

  return (
    <div
      ref={elRef}
      role="spinbutton"
      aria-label={ariaLabel}
      aria-valuenow={value}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => { dragRef.current.active = false; }}
      style={{
        cursor: "ew-resize",
        userSelect: "none",
        touchAction: "none",
        ...(width ? { width } : {}),
      }}
      className={cn(
        "inline-flex items-center justify-center px-1 rounded",
        "font-mono text-[11px] text-fg",
        "hover:bg-accent/10 transition-colors",
        className,
      )}
    >
      {round(value, precision)}
    </div>
  );
}

function round(v: number, p: number) {
  const m = Math.pow(10, p);
  return Math.round(v * m) / m;
}
