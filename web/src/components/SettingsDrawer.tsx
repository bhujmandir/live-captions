import { useState } from "react";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { buildOverlayUrl } from "@/settings";
import { Copy, Check, X, Wand2, Mic2 } from "lucide-react";
import { cn } from "@/lib/utils";

// Slide-out drawer from the right edge — "advanced" settings. Layout
// lives INLINE next to the stage preview (see LayoutPanel); this drawer
// is reserved for the less-frequently-tweaked knobs:
//   • Sarvam — STT model + VAD knobs (sent as `sarvam_cfg` on /api/start)
//   • Audio gate — local silence pre-filter (sent as `gate_cfg` on /api/start)
// Plus a "Copy overlay URL" action that mints the PP Web Object URL.

export function SettingsDrawer() {
  const open    = useStore((s) => s.settingsOpen);
  const setOpen = useStore((s) => s.setSettingsOpen);

  return (
    <>
      {/* Overlay backdrop — click to dismiss */}
      <div
        onClick={() => setOpen(false)}
        className={cn(
          "fixed inset-0 bg-black/40 backdrop-blur-[2px] z-40 transition-opacity",
          open ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
      />
      <aside
        className={cn(
          "fixed right-0 top-0 bottom-0 w-[26rem] max-w-[100vw]",
          "bg-surface border-l border-border z-50 shadow-2xl",
          "transition-transform overflow-hidden flex flex-col",
          open ? "translate-x-0" : "translate-x-full",
        )}
        aria-hidden={!open}
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="font-semibold text-sm flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-accent" /> Settings
          </h2>
          <Button variant="ghost" size="icon" onClick={() => setOpen(false)} title="Close">
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          <p className="text-xs text-fgMuted leading-relaxed -mt-1 mb-2">
            Layout (caption area, fonts, background, presets) is now inline on the
            Live tab so you can tweak it while watching the preview. Advanced STT
            and audio-gate settings live here.
          </p>
          <SarvamSection />
          <GateSection />
        </div>

        <footer className="border-t border-border p-3 space-y-2">
          <OverlayFitWidthToggle />
          <CopyOverlayUrlButton />
        </footer>
      </aside>
    </>
  );
}

// ── Sarvam (model + VAD) ────────────────────────────────────────────

function SarvamSection() {
  const s = useStore((st) => st.settings);
  const set = useStore((st) => st.updateSettings);
  return (
    <section>
      <SectionTitle icon={Wand2}>Sarvam STT</SectionTitle>
      <Field label="Model">
        <select
          value={s.sarvamModel}
          onChange={(e) => set({ sarvamModel: e.target.value })}
          className="h-9 w-full rounded-md border border-border bg-surface px-3 text-sm"
        >
          <option value="saaras:v3">saaras:v3 (default)</option>
          <option value="saaras:v2.5">saaras:v2.5 (legacy)</option>
          <option value="saaras:v2">saaras:v2 (legacy)</option>
        </select>
      </Field>
      <Row>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <Switch checked={s.sarvamHighVad} onCheckedChange={(v) => set({ sarvamHighVad: v })} />
          <span>High VAD sensitivity</span>
          <Badge variant="outline">~0.5s finalise</Badge>
        </label>
      </Row>
      <Row>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <Switch checked={s.sarvamVadSignals} onCheckedChange={(v) => set({ sarvamVadSignals: v })} />
          <span>VAD signals (partials)</span>
          <Badge variant="outline">drives "…"</Badge>
        </label>
      </Row>
    </section>
  );
}

// ── Audio gate (silence + hangover) ──────────────────────────────────

function GateSection() {
  const s = useStore((st) => st.settings);
  const set = useStore((st) => st.updateSettings);
  return (
    <section>
      <SectionTitle icon={Mic2}>Audio gate</SectionTitle>
      <p className="text-xs text-fgMuted mb-3">
        Client-side silence pre-filter. Cuts Sarvam credit burn during pauses.
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Num label="Silence %" min={0} max={100} step={0.1}
          value={s.silencePct}
          onChange={(v) => set({ silencePct: v })}
        />
        <Num label="Hangover (s)" min={0} max={10} step={0.1}
          value={s.hangoverSec}
          onChange={(v) => set({ hangoverSec: v })}
        />
      </div>
    </section>
  );
}

// ── Overlay: full-width toggle ──────────────────────────────────────

function OverlayFitWidthToggle() {
  const s   = useStore((st) => st.settings);
  const set = useStore((st) => st.updateSettings);
  return (
    <label className="flex items-center gap-2 text-xs cursor-pointer px-1">
      <Switch
        checked={s.overlayFitWidth}
        onCheckedChange={(v) => set({ overlayFitWidth: v })}
      />
      <span className="flex-1 whitespace-nowrap">Fill full browser width</span>
    </label>
  );
}

// ── Copy overlay URL button ─────────────────────────────────────────

function CopyOverlayUrlButton() {
  const s = useStore((st) => st.settings);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    const url = buildOverlayUrl(s);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback: prompt with the URL so the operator can manually copy.
      prompt("Copy this URL into ProPresenter's Web Object:", url);
    }
  };

  return (
    <Button onClick={copy} className="w-full" variant={copied ? "success" : "default"}>
      {copied ? <><Check className="h-4 w-4" /> Copied to clipboard</>
              : <><Copy  className="h-4 w-4" /> Copy overlay URL</>}
    </Button>
  );
}

// ── Tiny presentational helpers ─────────────────────────────────────

function SectionTitle({ children, icon: Icon }: { children: React.ReactNode; icon: React.ElementType }) {
  return (
    <h3 className="flex items-center gap-2 text-xs uppercase tracking-wider text-fgMuted font-semibold mb-3">
      <Icon className="h-3.5 w-3.5" /> {children}
    </h3>
  );
}

function SubTitle({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="flex items-center text-[11px] uppercase tracking-wider text-fgMuted mb-2 mt-3">
      {children}
    </h4>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <Label className="block mb-1">{label}</Label>
      {children}
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 flex items-center gap-3">{children}</div>;
}

function Num({ label, value, onChange, min, max, step }:
  { label: string; value: number; onChange: (v: number) => void;
    min?: number; max?: number; step?: number; }) {
  return (
    <div>
      <Label className="text-[10px]">{label}</Label>
      <Input
        type="number" min={min} max={max} step={step ?? 1}
        value={value}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className="h-9 mt-1"
      />
    </div>
  );
}
