import { useRef, useState } from "react";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ScrubNumber } from "@/components/ScrubNumber";
import { CAPTION_FONTS, buildOverlayUrl, toggleTextPanel, type Settings } from "@/settings";
import { Sparkles, Copy, Check, AlertTriangle } from "lucide-react";
import {
  linesThatFit, preferenceExceedsArea, largestFontSizeFor,
  observedArrivalMs, dwellFloorIsUnsafe, safeDwellCeilingMs,
} from "@/fit";

// Horizontal strip pinned above the stage preview. All non-spatial
// layout config lives here: preset, font, size, weight, lines,
// background, block toggle, plus Test render + Copy overlay URL.
//
// Spatial fields (X/Y/W/H of caption area + reserved block) are
// edited directly on the preview via DraggableRect, not in this bar.

export function FloatingToolbar() {
  const s   = useStore((st) => st.settings);
  const set = useStore((st) => st.updateSettings);
  const applyPreset = useStore((st) => st.applyLayoutPreset);
  const commit      = useStore((st) => st.commitSettings);
  const testRender  = useStore((st) => st.testRender);
  const [copied, setCopied] = useState(false);
  // Where the operator had the panel before they switched it off, so toggling
  // it off to compare and back on does not silently reset their choice.
  const lastPanelOpacity = useRef(0);

  // Say it out loud when the settings do not fit, rather than leaving the
  // operator to spot clipping by eye on a live output — which is how it
  // reached the sabha. The caption is never actually clipped now (the renderer
  // derives its line count), but silently showing fewer lines than were asked
  // for is still a surprise, and a surprise mid-katha is expensive.
  const clipping = preferenceExceedsArea(s.lines, s.areaH, s.fontSize);
  const fitting  = linesThatFit(s.areaH, s.fontSize);
  const maxFont  = largestFontSizeFor(s.lines, s.areaH);

  // ⚠️ The dwell trap, said out loud where the knob is.
  //
  // A floor at or above the LINE arrival interval makes every line wait longer
  // than the gap that feeds it, so lateness grows by the difference on every
  // line — a minute behind after sixty. That is exactly the fault the sabha saw
  // on Day 2 Morning, and it would be rebuilt on purpose.
  //
  // Checked against the interval MEASURED in this session, not a constant. A
  // hardcoded 4.0s would keep reassuring the operator long after the thing it
  // described had changed.
  const arrivalGaps = useStore((st) => st.arrivalGaps);
  const arrivalMs   = observedArrivalMs(arrivalGaps);
  const dwellUnsafe = dwellFloorIsUnsafe(s.lineMinDwellSec * 1000, arrivalMs);

  const copyOverlayUrl = async () => {
    try {
      await navigator.clipboard.writeText(buildOverlayUrl(s));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      prompt("Copy this URL into ProPresenter's Web Object:", buildOverlayUrl(s));
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3 py-2 border-b border-border bg-surface/80 backdrop-blur">
      <Group label="Preset">
        <select
          value={s.layoutPreset}
          onChange={(e) => {
            const v = e.target.value as Settings["layoutPreset"];
            if (v === "custom") set({ layoutPreset: "custom" });
            else applyPreset(v);
            commit();
          }}
          className="h-7 rounded border border-border bg-surface px-2 text-xs"
        >
          <option value="small">Small</option>
          <option value="medium">Medium</option>
          <option value="large">Large</option>
          <option value="custom">Custom</option>
        </select>
      </Group>

      <Divider />

      <Group label="Font">
        <select
          value={s.fontFamily}
          onChange={(e) => { set({ fontFamily: e.target.value }); commit(); }}
          title={CAPTION_FONTS.find((f) => f.id === s.fontFamily)?.note}
          style={{ fontFamily: CAPTION_FONTS.find((f) => f.id === s.fontFamily)?.stack }}
          className="h-7 rounded border border-border bg-surface px-2 text-xs"
        >
          {CAPTION_FONTS.map((f) => (
            <option key={f.id} value={f.id} style={{ fontFamily: f.stack }}>{f.name}</option>
          ))}
        </select>
      </Group>

      <Group label="Size">
        <ScrubNumber
          value={s.fontSize}
          onChange={(v) => set({ fontSize: v })}
          onCommit={commit}
          min={8} max={200}
          width={42}
          className="h-7 border border-border bg-surface px-1.5"
        />
      </Group>

      <Group label="Weight">
        <select
          value={s.fontWeight}
          onChange={(e) => { set({ fontWeight: parseInt(e.target.value, 10) }); commit(); }}
          className="h-7 rounded border border-border bg-surface px-2 text-xs"
        >
          {[300, 400, 500, 600, 700, 800].map((w) => (
            <option key={w} value={w}>{w}</option>
          ))}
        </select>
      </Group>

      <Group label="Lines">
        <ScrubNumber
          value={s.lines}
          onChange={(v) => set({ lines: Math.round(v) })}
          onCommit={commit}
          min={1} max={10}
          width={36}
          className="h-7 border border-border bg-surface px-1.5"
        />
        {clipping && (
          <span
            className="flex items-center gap-1 text-xs text-amber-500"
            title={`The caption area is ${s.areaH}px tall, which holds ${fitting} line${fitting === 1 ? "" : "s"} at ${s.fontSize}px. Showing ${fitting}. Make the area taller, or drop the font to ${maxFont}px.`}
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            showing {fitting}
          </span>
        )}
      </Group>

      <Divider />

      <Group label="Hold">
        <ScrubNumber
          value={s.lineMinDwellSec}
          onChange={(v) => set({ lineMinDwellSec: Math.round(v * 10) / 10 })}
          onCommit={commit}
          min={0.25} max={10} step={0.05} precision={2}
          width={44}
          className={`h-7 border bg-surface px-1.5 ${dwellUnsafe ? "border-red-500" : "border-border"}`}
        />
        <span className="text-xs text-fgMuted">s</span>
        {dwellUnsafe && arrivalMs !== null && (
          <span
            className="flex items-center gap-1 text-xs text-red-500"
            title={
              `Lines are arriving every ${(arrivalMs / 1000).toFixed(1)}s in this session. ` +
              `Holding each one for ${s.lineMinDwellSec}s means the scroll cannot keep up, ` +
              `and it will fall further behind the speaker with every line. ` +
              `Keep this at or below ${(safeDwellCeilingMs(arrivalMs) / 1000).toFixed(1)}s.`
            }
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            too slow for this speaker
          </span>
        )}
      </Group>

      <Group label="Clear after">
        <ScrubNumber
          value={s.captionMaxDwellSec}
          onChange={(v) => set({ captionMaxDwellSec: Math.round(v) })}
          onCommit={commit}
          min={1} max={120}
          width={40}
          className="h-7 border border-border bg-surface px-1.5"
        />
        <span className="text-xs text-fgMuted">s</span>
      </Group>

      <Divider />

      <Group label="Text panel">
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={s.textBgOpacity > 0}
            onChange={() => {
              const next = toggleTextPanel(s.textBgOpacity, lastPanelOpacity.current);
              if (s.textBgOpacity > 0) lastPanelOpacity.current = s.textBgOpacity;
              set({ textBgOpacity: next });
              commit();
            }}
          />
        </label>
        <ScrubNumber
          value={Math.round(s.textBgOpacity * 100)}
          onChange={(v) => set({ textBgOpacity: Math.max(0, Math.min(100, Math.round(v))) / 100 })}
          onCommit={commit}
          min={0} max={100}
          width={40}
          className="h-7 border border-border bg-surface px-1.5"
        />
        <span className="text-xs text-fgMuted">%</span>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer" title="One fixed rounded panel instead of one that hugs each line. Fixed never moves; only the words inside it change.">
          <input
            type="checkbox"
            checked={s.panelFixed}
            onChange={(e) => { set({ panelFixed: e.target.checked }); commit(); }}
          />
          <span className="text-fgMuted">fixed</span>
        </label>
      </Group>

      <Group label="Background">
        <label className="flex items-center gap-1.5 text-xs cursor-pointer">
          <input
            type="checkbox"
            checked={s.bg === "transparent"}
            onChange={(e) => { set({ bg: e.target.checked ? "transparent" : "#000000" }); commit(); }}
          />
          <span className="text-fgMuted">Transparent</span>
        </label>
        {s.bg !== "transparent" && (
          <input
            type="color"
            value={s.bg || "#000000"}
            onChange={(e) => set({ bg: e.target.value })}
            onBlur={commit}
            className="h-7 w-8 rounded border border-border bg-surface"
            title="Background colour (when not transparent)"
          />
        )}
      </Group>

      <Divider />

      <Group label="Block">
        <Switch
          checked={s.blockEnabled}
          onCheckedChange={(v) => { set({ blockEnabled: v }); commit(); }}
        />
      </Group>

      <div className="flex-1" />

      <Button variant="ghost" size="sm" onClick={() => testRender()}
        title="Render a fake FINAL to verify the display path">
        <Sparkles className="h-3.5 w-3.5" /> Test
      </Button>
      <Button
        variant={copied ? "success" : "secondary"}
        size="sm" onClick={copyOverlayUrl}
        title="Copy a URL for ProPresenter's Web Object (carries current layout)"
      >
        {copied
          ? <><Check className="h-3.5 w-3.5" /> Copied</>
          : <><Copy  className="h-3.5 w-3.5" /> Overlay URL</>}
      </Button>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[9px] uppercase tracking-tight text-fgMuted font-semibold whitespace-nowrap">{label}</span>
      {children}
    </div>
  );
}

function Divider() {
  return <span className="h-5 w-px bg-border" />;
}
