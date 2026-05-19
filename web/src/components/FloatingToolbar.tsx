import { useState } from "react";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ScrubNumber } from "@/components/ScrubNumber";
import { CAPTION_FONTS, buildOverlayUrl, type Settings } from "@/settings";
import { Sparkles, Copy, Check } from "lucide-react";

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
      </Group>

      <Divider />

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
