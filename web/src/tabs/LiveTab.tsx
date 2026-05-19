import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useStore } from "@/store";
import { api } from "@/api";
import type { AudioDevice } from "@/types";
import { CaptionRenderer } from "@/components/CaptionRenderer";
import { FloatingToolbar } from "@/components/FloatingToolbar";
import { DraggableRect, type Bounds, type SnapInfo } from "@/components/DraggableRect";
import { SnapGuides } from "@/components/SnapGuides";
import { TransportButton } from "@/components/TransportButton";
import {
  Mic, ArrowLeftRight, AlertCircle, FileAudio,
  Upload, Download, X, Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

const DEVICE_ID_KEY = "captions-device-id";
const STAGE: Bounds = { x: 0, y: 0, w: 1920, h: 1080 };

export function LiveTab() {
  const running      = useStore((s) => s.running);
  const audioPeak    = useStore((s) => s.audioPeak);
  const langSource   = useStore((s) => s.langSource);
  const langTarget   = useStore((s) => s.langTarget);
  const audioDevice  = useStore((s) => s.audioDevice);
  const audioSource  = useStore((s) => s.audioSource);
  const audioFile     = useStore((s) => s.audioFile);
  const audioFileName = useStore((s) => s.audioFileName);
  const lastSessionSrt = useStore((s) => s.lastSessionSrt);
  const setAudioSource = useStore((s) => s.setAudioSource);
  const setAudioFile   = useStore((s) => s.setAudioFile);
  const settings     = useStore((s) => s.settings);
  const updateSet    = useStore((s) => s.updateSettings);
  const commit       = useStore((s) => s.commitSettings);
  const undo         = useStore((s) => s.undoSettings);
  const setRunning   = useStore((s) => s.setRunning);
  const setLangs     = useStore((s) => s.setLangs);
  const sarvamLangs  = useStore((s) => s.sarvamLangs);
  const activeRect   = useStore((s) => s.activeRect);
  const setActiveRect = useStore((s) => s.setActiveRect);

  // Update both the local store and the server when a lang dropdown
  // changes. Without the optimistic local update the <select> snaps
  // back to its previous value because the dropdown is controlled by
  // store state and the server's WS broadcast races with the keypress.
  const changeLangs = (src: string, tgt: string) => {
    setLangs(src, tgt);
    api.setDirection(src, tgt).catch((e) => setErr(String(e?.message || e)));
  };

  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string>("");
  const [snaps, setSnaps] = useState<SnapInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadedSize, setUploadedSize] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleUpload = async (file: File) => {
    setErr(""); setUploading(true);
    try {
      const r = await api.uploadAudio(file);
      setAudioFile(r.path, r.name);
      setUploadedSize(r.size);
    } catch (e: any) {
      setErr(String(e?.message || e));
      setAudioFile("", "");
      setUploadedSize(null);
    } finally {
      setUploading(false);
    }
  };

  const clearFile = () => {
    setAudioFile("", "");
    setUploadedSize(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  // ── Devices + persistence ────────────────────────────────────
  useEffect(() => {
    api.listDevices().then((d) => {
      const list = d.devices || [];
      setDevices(list);
      const saved = (() => { try { return localStorage.getItem(DEVICE_ID_KEY) || ""; } catch { return ""; } })();
      const wanted = audioDevice ? String(audioDevice)
                   : saved && list.some((x) => String(x.id) === saved) ? saved
                   : (list.find((x) => x.default)?.id ?? list[0]?.id ?? "");
      setSelectedDevice(String(wanted));
    }).catch(() => {});
  }, [audioDevice]);

  useEffect(() => {
    if (selectedDevice) { try { localStorage.setItem(DEVICE_ID_KEY, selectedDevice); } catch {} }
  }, [selectedDevice]);

  // ── Stage scaling ────────────────────────────────────────────
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(1);
  useLayoutEffect(() => {
    if (!wrapRef.current) return;
    const recalc = () => {
      const el = wrapRef.current; if (!el) return;
      setScale(Math.min(el.clientWidth / 1920, el.clientHeight / 1080));
    };
    recalc();
    const ro = new ResizeObserver(recalc);
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  // ── Keyboard: nudge, undo ────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Skip if focus is inside a text input — operator may be typing.
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;

      // Cmd-Z / Ctrl-Z anywhere on the page → undo last commit.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
        return;
      }
      // Arrow nudging only when a rect is selected.
      if (!activeRect) return;
      const step = e.shiftKey ? 10 : 1;
      let dx = 0, dy = 0;
      if (e.key === "ArrowLeft")  dx = -step;
      if (e.key === "ArrowRight") dx =  step;
      if (e.key === "ArrowUp")    dy = -step;
      if (e.key === "ArrowDown")  dy =  step;
      if (!dx && !dy) return;
      e.preventDefault();
      if (activeRect === "area") {
        updateSet({
          areaX: clamp(settings.areaX + dx, 0, 1920 - settings.areaW),
          areaY: clamp(settings.areaY + dy, 0, 1080 - settings.areaH),
        });
      } else if (activeRect === "block") {
        // Block constrained to caption area.
        updateSet({
          blockX: clamp(settings.blockX + dx, settings.areaX, settings.areaX + settings.areaW - settings.blockW),
          blockY: clamp(settings.blockY + dy, settings.areaY, settings.areaY + settings.areaH - settings.blockH),
        });
      }
      commit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeRect, settings, updateSet, commit, undo]);

  const handleStart = async () => {
    setErr(""); setBusy(true); setRunning(true);
    try {
      const body: any = {
        source: audioSource,
        source_lang: langSource,
        target_lang: langTarget,
        sarvam: {
          model:                settings.sarvamModel,
          high_vad_sensitivity: settings.sarvamHighVad,
          vad_signals:          settings.sarvamVadSignals,
        },
        gate: {
          silence_threshold: settings.silencePct / 100.0,
          hangover_sec:      settings.hangoverSec,
        },
      };
      if (audioSource === "device") body.device = selectedDevice;
      else                          body.file   = audioFile;
      await api.startCapture(body);
    } catch (e: any) {
      setErr(String(e.message || e));
      setRunning(false);
    } finally { setBusy(false); }
  };

  const handleStop = async () => {
    setBusy(true); setRunning(false);
    try { await api.stopCapture(); }
    catch (e: any) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const handleFlip = async () => {
    setErr(""); setBusy(true);
    // Local-first flip so the dropdowns update without a round trip.
    setLangs(langTarget, langSource);
    try { await api.setDirection(langTarget, langSource); }
    catch (e: any) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  // ── Bounds for the two draggable rects ───────────────────────
  const areaBounds:  Bounds = { x: settings.areaX, y: settings.areaY, w: settings.areaW, h: settings.areaH };
  const blockBounds: Bounds = { x: settings.blockX, y: settings.blockY, w: settings.blockW, h: settings.blockH };

  return (
    <div className="h-full grid grid-rows-[auto_1fr_auto] min-h-0">
      <FloatingToolbar />

      {/* Stage preview with draggable rects */}
      <div className="relative bg-bg overflow-hidden">
        <div
          ref={wrapRef}
          className="absolute inset-0 grid place-items-center"
          onClick={() => setActiveRect(null)}
        >
          <div
            className="relative bg-black/80"
            style={{
              width:  1920 * scale,
              height: 1080 * scale,
              boxShadow: "0 0 0 1px rgba(255,255,255,0.06)",
            }}
            onClick={(e) => { e.stopPropagation(); setActiveRect(null); }}
          >
            {/* Caption renderer — read-only at this layer */}
            <CaptionRenderer stageScale={scale} showGuides={false} />

            {/* Overlay for direct manipulation. Everything inside is in
                STAGE coordinates; we wrap it in a transform that matches
                the preview's scale so 1920×1080 fits inside the visible
                preview. */}
            <div style={{
              position: "absolute", left: 0, top: 0,
              width: 1920, height: 1080,
              transform: `scale(${scale})`,
              transformOrigin: "top left",
              pointerEvents: "none",
            }}>
              <SnapGuides snaps={snaps} stageScale={scale} stageW={1920} stageH={1080} />

              {/* Caption area is always draggable */}
              <DraggableRect
                bounds={areaBounds}
                onChange={(b) => updateSet({ areaX: b.x, areaY: b.y, areaW: b.w, areaH: b.h })}
                onCommit={commit}
                selected={activeRect === "area"}
                onSelect={() => setActiveRect("area")}
                stageBounds={STAGE}
                stageScale={scale}
                color="#ff8c00"
                label="caption area"
                onSnap={setSnaps}
              />

              {/* Reserved block: only shown when enabled. Clamped to the
                  caption area so it can't drift outside the wrap-region. */}
              {settings.blockEnabled && (
                <DraggableRect
                  bounds={blockBounds}
                  onChange={(b) => updateSet({ blockX: b.x, blockY: b.y, blockW: b.w, blockH: b.h })}
                  onCommit={commit}
                  selected={activeRect === "block"}
                  onSelect={() => setActiveRect("block")}
                  stageBounds={STAGE}
                  containerBounds={areaBounds}
                  stageScale={scale}
                  color="#2196f3"
                  label="reserved block"
                  onSnap={setSnaps}
                />
              )}
            </div>
          </div>
        </div>

        {/* Audio meter */}
        {running && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-3 px-3 py-1.5 rounded-full bg-surface border border-border z-10">
            <span className="font-mono text-[10px] uppercase tracking-wider text-fgMuted">audio</span>
            <div className="w-40 h-1.5 bg-elevated rounded-full overflow-hidden">
              <div
                className={cn(
                  "h-full transition-all",
                  (audioPeak ?? 0) > 0.5 ? "bg-warn"
                  : (audioPeak ?? 0) > 0.01 ? "bg-success" : "bg-muted",
                )}
                style={{ width: `${Math.min(100, (audioPeak ?? 0) * 100).toFixed(1)}%` }}
              />
            </div>
            <span className="font-mono text-xs text-fgMuted w-8 text-right">
              {audioPeak == null ? "—" : `${Math.round((audioPeak) * 100)}%`}
            </span>
          </div>
        )}
      </div>

      {/* Bottom control bar */}
      <div className="border-t border-border bg-surface p-4">
        {err && (
          <div className="mb-3 flex items-start gap-2 rounded border border-danger/40 bg-danger/10 p-2.5 text-sm text-danger">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <span>{err}</span>
          </div>
        )}

        {!running && lastSessionSrt && (
          <div className="mb-3 flex items-center gap-3 rounded border border-success/40 bg-success/10 p-2.5 text-sm">
            <Download className="h-4 w-4 shrink-0 text-success" />
            <span className="flex-1">Session complete — SRT ready to download.</span>
            <Button variant="success" size="sm" asChild>
              <a href={lastSessionSrt}
                download={lastSessionSrt.split("/").pop() || "captions.srt"}>
                <Download className="h-4 w-4" /> Download SRT
              </a>
            </Button>
          </div>
        )}

        <div className="grid grid-cols-[auto_1fr_1fr_auto_auto] gap-3 items-end">
          <div className="flex flex-col gap-1.5">
            <Label>Source</Label>
            <div className="flex rounded-md border border-border overflow-hidden">
              <button
                onClick={() => setAudioSource("device")}
                disabled={running}
                className={cn(
                  "px-3 h-9 text-xs flex items-center gap-1.5 transition-colors",
                  audioSource === "device" ? "bg-elevated text-fg" : "text-fgMuted hover:text-fg",
                )}
              >
                <Mic className="h-3.5 w-3.5" /> Mic
              </button>
              <button
                onClick={() => setAudioSource("file")}
                disabled={running}
                className={cn(
                  "px-3 h-9 text-xs flex items-center gap-1.5 transition-colors border-l border-border",
                  audioSource === "file" ? "bg-elevated text-fg" : "text-fgMuted hover:text-fg",
                )}
              >
                <FileAudio className="h-3.5 w-3.5" /> File
              </button>
            </div>
          </div>

          {audioSource === "device" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="device">Microphone</Label>
              <select
                id="device"
                value={selectedDevice}
                onChange={(e) => setSelectedDevice(e.target.value)}
                disabled={running}
                className="h-9 rounded-md border border-border bg-surface px-3 text-sm disabled:opacity-50"
              >
                {devices.length === 0 && <option value="">No devices found</option>}
                {devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}{d.default ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label>Audio file</Label>
              <input
                ref={fileInputRef}
                type="file"
                accept="audio/*,video/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleUpload(f);
                }}
              />
              {audioFile ? (
                <div className="h-9 rounded-md border border-border bg-surface px-3 flex items-center gap-2 text-sm">
                  <FileAudio className="h-4 w-4 text-accent shrink-0" />
                  <span className="truncate flex-1">{audioFileName || audioFile}</span>
                  {uploadedSize != null && (
                    <span className="font-mono text-xs text-fgMuted shrink-0">
                      {formatBytes(uploadedSize)}
                    </span>
                  )}
                  {!running && (
                    <button
                      type="button"
                      onClick={clearFile}
                      className="text-fgMuted hover:text-fg"
                      title="Remove"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  className="h-9 justify-start"
                  disabled={running || uploading}
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uploading
                    ? <><Loader2 className="h-4 w-4 animate-spin" /> Uploading…</>
                    : <><Upload className="h-4 w-4" /> Choose audio file…</>}
                </Button>
              )}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label>Language</Label>
            <div className="flex items-center gap-2">
              <select
                value={langSource}
                onChange={(e) => changeLangs(e.target.value, langTarget)}
                className="flex-1 h-9 rounded-md border border-border bg-surface px-3 text-sm"
              >
                {sarvamLangs.map(([code, name]) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
              <Button variant="ghost" size="icon" onClick={handleFlip}
                disabled={busy} title="Swap source and target">
                <ArrowLeftRight className="h-4 w-4" />
              </Button>
              <select
                value={langTarget}
                onChange={(e) => changeLangs(langSource, e.target.value)}
                className="flex-1 h-9 rounded-md border border-border bg-surface px-3 text-sm"
              >
                {sarvamLangs.map(([code, name]) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
            </div>
          </div>

          <div />

          <TransportButton
            running={running}
            disabled={busy || (!running && (audioSource === "device" ? !selectedDevice : !audioFile))}
            onClick={running ? handleStop : handleStart}
          />
        </div>
      </div>
    </div>
  );
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
