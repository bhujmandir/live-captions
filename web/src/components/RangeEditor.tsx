import { useEffect, useMemo, useRef, useState } from "react";
import type { VodRange } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Play, Plus, Trash2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// Editable list of in/out ranges over an MP4. Operator picks which
// sections of a downloaded video to transcribe — saves STT cost and
// avoids transcribing kirtans / bhajans / intermissions.
//
// Two parallel surfaces edit the same `ranges` array:
//   • Visual timeline below the video — each range is a draggable bar
//     with left + right resize handles. Click empty timeline to add a
//     range starting at the click point.
//   • Time-input rows below the timeline — typed HH:MM:SS values for
//     precise editing, plus + Add range / × Delete controls.
//
// "Transcribe selected" submits the ranges. "Transcribe full video"
// submits an empty array (server interprets that as the whole VOD).

type Props = {
  mp4Url:   string;
  initial?: VodRange[];
  onSubmit: (ranges: VodRange[]) => void | Promise<void>;
  onCancel?: () => void;
  busy?:    boolean;
};

export function RangeEditor({ mp4Url, initial = [], onSubmit, onCancel, busy = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [duration, setDuration] = useState(0);
  const [playhead, setPlayhead] = useState(0);
  const [ranges, setRanges] = useState<VodRange[]>(initial);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    const onMeta = () => setDuration(el.duration || 0);
    const onTime = () => setPlayhead(el.currentTime || 0);
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("timeupdate", onTime);
    return () => {
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("timeupdate", onTime);
    };
  }, []);

  const totalSelected = useMemo(
    () => ranges.reduce((s, r) => s + Math.max(0, r.end_s - r.start_s), 0),
    [ranges],
  );

  const addRange = (atTime?: number) => {
    const start = atTime != null ? atTime : playhead;
    // Default new-range duration: 30s OR the remaining video, whichever is shorter.
    const end = Math.min(duration || start + 30, start + 30);
    setRanges((r) => [...r, { start_s: round(start), end_s: round(end) }].sort((a, b) => a.start_s - b.start_s));
  };

  const updateRange = (idx: number, patch: Partial<VodRange>) => {
    setRanges((r) => {
      const next = r.map((x, i) => (i === idx ? { ...x, ...patch } : x));
      // Re-sort by start so the rows always match timeline visual order.
      return next.sort((a, b) => a.start_s - b.start_s);
    });
  };
  const deleteRange = (idx: number) => setRanges((r) => r.filter((_, i) => i !== idx));

  const seekTo = (t: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = Math.max(0, Math.min(duration, t));
    }
  };

  const playRange = (r: VodRange) => {
    if (!videoRef.current) return;
    videoRef.current.currentTime = r.start_s;
    videoRef.current.play().catch(() => {});
  };

  return (
    <div className="space-y-4">
      {/* Video preview */}
      <div className="rounded-md border border-border bg-black overflow-hidden">
        <video
          ref={videoRef}
          src={mp4Url}
          controls
          preload="metadata"
          className="w-full max-h-[55vh]"
        />
      </div>

      {/* Timeline strip */}
      <TimelineStrip
        duration={duration}
        playhead={playhead}
        ranges={ranges}
        onUpdate={updateRange}
        onSeek={seekTo}
        onAddAt={(t) => addRange(t)}
      />

      {/* Quick actions */}
      <div className="flex items-center gap-2 text-sm text-fgMuted">
        <Button size="sm" variant="ghost" onClick={() => addRange()}>
          <Plus className="h-3.5 w-3.5" /> Add range at playhead ({fmtTime(playhead)})
        </Button>
        <span className="text-[10px]">·</span>
        <span className="text-[10px] font-mono">
          {ranges.length} range(s) · {fmtDuration(totalSelected)} of {fmtTime(duration)}
        </span>
      </div>

      {/* Per-range editor rows */}
      <div className="space-y-1.5">
        {ranges.length === 0 && (
          <div className="text-xs text-fgMuted italic px-2">
            No ranges picked yet. Click the timeline to add a 30s range, drag the
            edges to resize, or use "Transcribe full video" below to transcribe everything.
          </div>
        )}
        {ranges.map((r, i) => (
          <RangeRow
            key={i}
            idx={i}
            range={r}
            duration={duration}
            onChange={(p) => updateRange(i, p)}
            onDelete={() => deleteRange(i)}
            onPlay={() => playRange(r)}
          />
        ))}
      </div>

      {/* Submit row */}
      <div className="flex items-center gap-2 pt-2 border-t border-border">
        <Button
          variant="primary" size="lg"
          disabled={busy || ranges.length === 0 || ranges.some((r) => r.end_s <= r.start_s)}
          onClick={() => onSubmit(ranges)}
        >
          {busy
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Submitting…</>
            : <>Transcribe selected · {fmtDuration(totalSelected)}</>}
        </Button>
        <Button
          variant="outline" size="lg" disabled={busy}
          onClick={() => onSubmit([])}
          title="Transcribe the whole video (no range filter)"
        >
          Transcribe full video
        </Button>
        {onCancel && (
          <Button variant="ghost" size="lg" onClick={onCancel} disabled={busy}>
            Cancel job
          </Button>
        )}
      </div>
    </div>
  );
}

// ── Timeline strip ──────────────────────────────────────────────────

type DragMode = null | { kind: "move" | "left" | "right"; idx: number; pointerId: number };

function TimelineStrip({
  duration, playhead, ranges, onUpdate, onSeek, onAddAt,
}: {
  duration: number;
  playhead: number;
  ranges: VodRange[];
  onUpdate: (idx: number, patch: Partial<VodRange>) => void;
  onSeek: (t: number) => void;
  onAddAt: (t: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragMode>(null);
  const dragStartRef = useRef<{ x: number; range: VodRange }>({ x: 0, range: { start_s: 0, end_s: 0 } });

  const xToTime = (clientX: number) => {
    const el = trackRef.current;
    if (!el || !duration) return 0;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return frac * duration;
  };

  const startDrag = (e: React.PointerEvent, kind: "move" | "left" | "right", idx: number) => {
    if (e.button !== 0) return;
    dragStartRef.current = { x: e.clientX, range: { ...ranges[idx] } };
    setDrag({ kind, idx, pointerId: e.pointerId });
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    e.stopPropagation();
    e.preventDefault();
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const el = trackRef.current;
    if (!el || !duration) return;
    const rect = el.getBoundingClientRect();
    const dxFrac = (e.clientX - dragStartRef.current.x) / rect.width;
    const dxTime = dxFrac * duration;
    const orig = dragStartRef.current.range;
    let next: VodRange = { ...orig };
    if (drag.kind === "move") {
      next.start_s = clamp(orig.start_s + dxTime, 0, duration - (orig.end_s - orig.start_s));
      next.end_s   = next.start_s + (orig.end_s - orig.start_s);
    } else if (drag.kind === "left") {
      next.start_s = clamp(orig.start_s + dxTime, 0, orig.end_s - 1);
    } else if (drag.kind === "right") {
      next.end_s   = clamp(orig.end_s + dxTime, orig.start_s + 1, duration);
    }
    onUpdate(drag.idx, { start_s: round(next.start_s), end_s: round(next.end_s) });
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (!drag) return;
    try { (e.target as HTMLElement).releasePointerCapture(drag.pointerId); } catch { /* ignore */ }
    setDrag(null);
  };

  const onTrackClick = (e: React.MouseEvent) => {
    if (drag) return;
    const t = xToTime(e.clientX);
    // Holding shift on click adds a new range; plain click seeks.
    if (e.shiftKey) onAddAt(t);
    else            onSeek(t);
  };

  const pctOf = (t: number) => duration > 0 ? (t / duration) * 100 : 0;

  return (
    <div className="space-y-1">
      <div
        ref={trackRef}
        onClick={onTrackClick}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        className="relative h-12 rounded bg-elevated border border-border cursor-crosshair select-none"
        style={{ touchAction: "none" }}
        title="Click to seek · Shift-click to add a new range here"
      >
        {/* Range bars */}
        {ranges.map((r, idx) => (
          <div
            key={idx}
            onPointerDown={(e) => startDrag(e, "move", idx)}
            className="absolute top-1.5 bottom-1.5 bg-accent/40 border-l border-r border-accent rounded-sm cursor-grab"
            style={{
              left:  `${pctOf(r.start_s)}%`,
              width: `${Math.max(0.3, pctOf(r.end_s - r.start_s))}%`,
            }}
          >
            {/* Left resize handle */}
            <div
              onPointerDown={(e) => startDrag(e, "left", idx)}
              className="absolute left-0 top-0 bottom-0 w-1.5 bg-accent cursor-ew-resize"
            />
            {/* Right resize handle */}
            <div
              onPointerDown={(e) => startDrag(e, "right", idx)}
              className="absolute right-0 top-0 bottom-0 w-1.5 bg-accent cursor-ew-resize"
            />
            <span className="absolute top-0 left-2 text-[9px] font-mono text-accentFg pointer-events-none">
              {fmtTime(r.start_s)} – {fmtTime(r.end_s)}
            </span>
          </div>
        ))}
        {/* Playhead marker */}
        {duration > 0 && (
          <div
            className="absolute top-0 bottom-0 w-px bg-warn pointer-events-none"
            style={{ left: `${pctOf(playhead)}%` }}
          />
        )}
      </div>
      {/* Tick labels */}
      <div className="relative h-3 text-[9px] font-mono text-fgMuted">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <span
            key={f}
            className="absolute -translate-x-1/2"
            style={{ left: `${f * 100}%` }}
          >
            {fmtTime(f * duration)}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Per-range row (text input fallback + delete) ────────────────────

function RangeRow({
  idx, range, duration, onChange, onDelete, onPlay,
}: {
  idx: number;
  range: VodRange;
  duration: number;
  onChange: (patch: Partial<VodRange>) => void;
  onDelete: () => void;
  onPlay: () => void;
}) {
  const invalid = range.end_s <= range.start_s;
  return (
    <div className={cn(
      "flex items-center gap-2 rounded border p-2 bg-surface",
      invalid ? "border-danger/60" : "border-border",
    )}>
      <Badge variant="outline" className="text-[10px] tabular-nums w-8 text-center">#{idx + 1}</Badge>
      <TimeInput
        value={range.start_s} label="In"
        max={range.end_s}
        onChange={(v) => onChange({ start_s: v })}
      />
      <span className="text-fgMuted">→</span>
      <TimeInput
        value={range.end_s} label="Out"
        min={range.start_s}
        max={duration}
        onChange={(v) => onChange({ end_s: v })}
      />
      <span className="text-[11px] font-mono text-fgMuted w-20">
        {fmtDuration(Math.max(0, range.end_s - range.start_s))}
      </span>
      <div className="flex-1" />
      <Button variant="ghost" size="icon" onClick={onPlay} title="Play this range">
        <Play className="h-4 w-4" />
      </Button>
      <Button variant="ghost" size="icon" onClick={onDelete} title="Delete this range">
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  );
}

// ── Time input — HH:MM:SS text with parse/format ────────────────────

function TimeInput({
  value, onChange, label, min, max,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState(fmtTime(value));
  const [editing, setEditing] = useState(false);
  useEffect(() => { if (!editing) setDraft(fmtTime(value)); }, [value, editing]);

  return (
    <label className="flex items-center gap-1.5 text-xs">
      <span className="text-fgMuted text-[10px] uppercase tracking-wider">{label}</span>
      <Input
        type="text"
        value={draft}
        onFocus={() => setEditing(true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setEditing(false);
          const t = parseTime(draft);
          if (t == null) { setDraft(fmtTime(value)); return; }
          let n = t;
          if (min != null) n = Math.max(min, n);
          if (max != null) n = Math.min(max, n);
          onChange(round(n));
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") { setDraft(fmtTime(value)); setEditing(false); (e.target as HTMLInputElement).blur(); }
        }}
        className="h-7 w-24 font-mono text-[11px] text-center"
      />
    </label>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────

function round(v: number, places = 1) {
  const m = Math.pow(10, places);
  return Math.round(v * m) / m;
}
function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

/** Format seconds → "HH:MM:SS" (no fractions). Handles 0 + NaN. */
function fmtTime(secs: number): string {
  if (!Number.isFinite(secs) || secs < 0) secs = 0;
  const total = Math.floor(secs);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

/** Friendlier "12m 34s" / "1h 23m" / "45s" for durations. */
function fmtDuration(secs: number): string {
  if (!Number.isFinite(secs) || secs <= 0) return "0s";
  const t = Math.floor(secs);
  if (t < 60) return `${t}s`;
  const m = Math.floor(t / 60), s = t % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

function pad(n: number) { return n.toString().padStart(2, "0"); }

/** Parse "HH:MM:SS" or "MM:SS" or "S(.S)" → seconds. null if invalid. */
function parseTime(s: string): number | null {
  s = (s || "").trim();
  if (!s) return null;
  const parts = s.split(":").map((p) => p.trim());
  if (parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  const nums = parts.map(Number);
  if (nums.length === 1) return nums[0];
  if (nums.length === 2) return nums[0] * 60 + nums[1];
  if (nums.length === 3) return nums[0] * 3600 + nums[1] * 60 + nums[2];
  return null;
}
