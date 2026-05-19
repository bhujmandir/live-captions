import { create } from "zustand";
import type { Feed, Rule, VodJob, TranscriptEntry, AppConfig, LogRow } from "./types";
import { loadSettings, saveSettings, type Settings, inferLayoutPreset, PRESETS } from "./settings";
import { api } from "./api";

// Single Zustand store — small enough that the whole app reads from it
// and the few writes are co-located in the WS handler in api.ts.

type Connection = "connecting" | "open" | "closed";

type State = {
  // Server-rendered boot config (branding + lang matrix + defaults).
  // Populated by applyConfig() right after /api/config returns, before
  // the React tree mounts. Held here so any component can read the
  // current accent/branding without re-fetching.
  appName:     string;
  sarvamLangs: Array<[string, string]>;
  mayuraLangs: string[];
  // In-browser debug log — fed by the WS `log_snapshot` (on connect)
  // and `log` (live) events. Bounded to MAX_DEBUG_LOGS so a chatty
  // process doesn't grow the store unbounded.
  debugLogs: LogRow[];
  // Connection / session
  conn: Connection;
  running: boolean;
  langSource: string;
  langTarget: string;
  audioPeak: number | null;
  audioDevice?: string;
  audioSource: "device" | "file";
  audioFile: string;
  // Friendly name of the most recently uploaded audio file, shown next
  // to the file picker. Empty when audioFile is empty.
  audioFileName: string;
  // Static-mount URLs broadcast on `session_saved`. Cleared whenever a
  // new session starts so a stale link from the previous run can't be
  // mistaken for the current one. The LiveTab uses these to surface a
  // "Download SRT" button once the file-mode playback finishes.
  lastSessionSrt:   string | null;
  lastSessionJsonl: string | null;
  // Live caption display
  partialActive: boolean;
  captionText: string;          // last 6000 chars of accumulated FINALs
  // Transcript log
  transcript: TranscriptEntry[];
  // Registries (mirror server state)
  feeds: Feed[];
  rules: Rule[];
  vodJobs: VodJob[];
  // UI
  tab: "live" | "outputs" | "rules" | "reprocess" | "transcript";
  settingsOpen: boolean;
  debugOpen: boolean;
  // Layout + Sarvam + audio settings. Persisted to localStorage; URL
  // params (overlay mode) override on load.
  settings: Settings;
  // Direct-manipulation UI state for the Live tab's preview.
  activeRect: "area" | "block" | null;
  // Bounded undo stack — only stable commits (drag-end, scrub-end,
  // toolbar field commits) push entries here so transient mid-drag
  // updates don't pollute history.
  settingsHistory: Settings[];

  // Actions
  applyConfig: (cfg: AppConfig) => void;
  setLogSnapshot: (logs: LogRow[]) => void;
  pushLog:        (row: LogRow) => void;
  clearLogs:      () => void;
  setConn: (c: Connection) => void;
  setTab: (t: State["tab"]) => void;
  setRunning: (r: boolean) => void;
  setLangs: (s: string, t: string) => void;
  setAudioPeak: (p: number | null) => void;
  setAudioDevice: (d: string | undefined) => void;
  setAudioSource: (s: "device" | "file") => void;
  setAudioFile: (f: string, name?: string) => void;
  setLastSession: (srt: string | null, jsonl: string | null) => void;
  setPartialActive: (a: boolean) => void;
  pushFinal: (text: string, raw: string, rulesFired: string[]) => void;
  clearCaption: () => void;
  setFeeds: (f: Feed[]) => void;
  setRules: (r: Rule[]) => void;
  setVodJobs: (j: VodJob[]) => void;
  upsertVodJob: (j: VodJob) => void;
  removeVodJob: (id: string) => void;
  clearTranscript: () => void;
  setSettingsOpen: (o: boolean) => void;
  setDebugOpen:    (o: boolean) => void;
  updateSettings: (patch: Partial<Settings>) => void;
  applyLayoutPreset: (name: "small" | "medium" | "large") => void;
  testRender: (text?: string) => void;
  setActiveRect: (r: "area" | "block" | null) => void;
  commitSettings: () => void;       // push current settings to undo stack
  undoSettings: () => void;
};

const MAX_CAPTION_LEN = 6000;
const MAX_TRANSCRIPT  = 500;
const MAX_DEBUG_LOGS  = 400;     // matches server-side _recent_logs deque size

// Initial settings come from URL params (overlay) or localStorage, with
// defaults falling back. Loaded once at module init.
const _initialSettings = loadSettings();

export const useStore = create<State>((set, get) => ({
  appName:     "Captions",
  sarvamLangs: [],
  mayuraLangs: [],
  debugLogs:   [],
  conn: "connecting",
  running: false,
  langSource: _initialSettings.source,
  langTarget: _initialSettings.target,
  audioPeak: null,
  audioDevice: undefined,
  audioSource: "device",
  audioFile: "",
  audioFileName: "",
  lastSessionSrt:   null,
  lastSessionJsonl: null,
  partialActive: false,
  captionText: "",
  transcript: [],
  feeds: [],
  rules: [],
  vodJobs: [],
  tab: "live",
  settingsOpen: false,
  debugOpen: false,
  settings: _initialSettings,
  activeRect: null,
  settingsHistory: [],

  applyConfig: (cfg) => {
    document.title = cfg.appName;
    document.documentElement.style.setProperty("--accent", cfg.accentHsl);
    // First-ever load (nothing in localStorage) seeds the lang pair from
    // server defaults. After that the operator's persisted pair wins so
    // we don't trample their choice on every refresh.
    const hasStored = !!localStorage.getItem("captions-settings");
    set((st) => {
      const settings = hasStored
        ? st.settings
        : { ...st.settings, source: cfg.defaultSource, target: cfg.defaultTarget };
      if (!hasStored) saveSettings(settings);
      return {
        appName:     cfg.appName,
        sarvamLangs: cfg.sarvamLangs,
        mayuraLangs: cfg.mayuraLangs,
        langSource:  settings.source,
        langTarget:  settings.target,
        settings,
      };
    });
  },
  setLogSnapshot: (logs) => set({
    debugLogs: logs.slice(-MAX_DEBUG_LOGS),
  }),
  pushLog: (row) => set((st) => {
    const next = [...st.debugLogs, row];
    if (next.length > MAX_DEBUG_LOGS) next.splice(0, next.length - MAX_DEBUG_LOGS);
    return { debugLogs: next };
  }),
  clearLogs: () => set({ debugLogs: [] }),
  setConn: (c) => set({ conn: c }),
  setTab: (t) => set({ tab: t }),
  setRunning: (r) => set((st) => {
    // On Start, clear the previous run's SRT so it can't be downloaded
    // by mistake mid-session. The new path arrives via session_saved
    // when the session ends.
    if (r && !st.running) {
      return { running: true, lastSessionSrt: null, lastSessionJsonl: null };
    }
    return { running: r };
  }),
  setLangs: (s, t) => set((st) => {
    // Mirror lang changes into persisted settings so a refresh keeps the
    // operator's last pair.
    const settings = { ...st.settings, source: s, target: t };
    saveSettings(settings);
    return { langSource: s, langTarget: t, settings };
  }),
  setAudioPeak: (p) => set({ audioPeak: p }),
  setAudioDevice: (d) => set({ audioDevice: d }),
  setAudioSource: (s) => set({ audioSource: s }),
  setAudioFile:   (f, name) => set((st) => ({
    audioFile: f,
    audioFileName: name !== undefined ? name : (f ? st.audioFileName : ""),
  })),
  setLastSession: (srt, jsonl) => set({ lastSessionSrt: srt, lastSessionJsonl: jsonl }),
  setPartialActive: (a) => set({ partialActive: a }),
  pushFinal: (text, raw, rulesFired) => set((st) => {
    let captionText = (st.captionText ? st.captionText + " " : "") + text;
    if (captionText.length > MAX_CAPTION_LEN) captionText = captionText.slice(-MAX_CAPTION_LEN);
    const transcript = [...st.transcript, { ts: new Date().toISOString(), text, raw, rulesFired }];
    if (transcript.length > MAX_TRANSCRIPT) transcript.splice(0, transcript.length - MAX_TRANSCRIPT);
    return { captionText, transcript };
  }),
  clearCaption: () => set({ captionText: "" }),
  setFeeds: (f) => set({ feeds: f }),
  setRules: (r) => set({ rules: r }),
  setVodJobs: (j) => set({ vodJobs: j }),
  upsertVodJob: (j) => set((st) => {
    const next = st.vodJobs.filter((x) => x.id !== j.id);
    next.unshift(j);
    next.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
    return { vodJobs: next };
  }),
  removeVodJob: (id) => set((st) => ({ vodJobs: st.vodJobs.filter((j) => j.id !== id) })),
  clearTranscript: () => set({ transcript: [] }),
  setSettingsOpen: (o) => set({ settingsOpen: o }),
  setDebugOpen:    (o) => set({ debugOpen: o }),
  updateSettings: (patch) => set((st) => {
    const merged = { ...st.settings, ...patch };
    // Only re-infer the preset when a preset-defining field actually
    // changed. Otherwise (font family, bg colour, Sarvam knobs, gate)
    // the operator's explicit preset choice is preserved.
    const PRESET_FIELDS = ["areaX","areaY","areaW","areaH","fontSize","fontWeight","lines"] as const;
    if (PRESET_FIELDS.some((k) => k in (patch as object))) {
      merged.layoutPreset = inferLayoutPreset(merged);
    }
    saveSettings(merged);
    return { settings: merged };
  }),
  applyLayoutPreset: (name) => set((st) => {
    const preset = PRESETS[name];
    const merged = { ...st.settings, ...preset, layoutPreset: name };
    saveSettings(merged);
    return { settings: merged };
  }),
  testRender: (text) => {
    const samples = [
      "Testing live caption rendering …",
      "This is a synthetic FINAL pushed by the operator UI.",
      "The fox jumps over the lazy dog — Sarvam not involved.",
    ];
    const t = text || samples[Math.floor(Math.random() * samples.length)];
    // Route via the server so the broadcast reaches every connected
    // tab (operator surface + overlay served at /?overlay=1). The
    // server posts a FINAL frame onto the existing Broadcaster; the
    // WS handler updates each tab's store identically to a real
    // Sarvam-returned caption.
    api.testRender(text).catch(() => {
      // Server unreachable → fall back to local-only render so the
      // operator at least sees something on their preview.
      get().pushFinal(t, t, []);
    });
  },
  setActiveRect: (r) => set({ activeRect: r }),
  commitSettings: () => set((st) => {
    // Cap the undo stack at 20 entries — the operator isn't going to
    // hand-step further than that, and unbounded growth eats memory.
    const next = [...st.settingsHistory, st.settings];
    if (next.length > 20) next.shift();
    return { settingsHistory: next };
  }),
  undoSettings: () => set((st) => {
    if (!st.settingsHistory.length) return {};
    const next = [...st.settingsHistory];
    const prev = next.pop()!;
    saveSettings(prev);
    return { settings: prev, settingsHistory: next };
  }),
}));
