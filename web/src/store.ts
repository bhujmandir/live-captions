import { create, type StateCreator } from "zustand";
import type { Feed, Rule, VodJob, TranscriptEntry, AppConfig, LogRow, LinkState, LinkReason, Connection } from "./types";
import { loadSettings, saveSettings, type Settings, inferLayoutPreset, PRESETS, DEFAULTS } from "./settings";
import { api } from "./api";
import { params } from "./env";
import { splitIntoLines, estimateCharsPerLine } from "./fit";

// Single Zustand store — small enough that the whole app reads from it
// and the few writes are co-located in the WS handler in api.ts.

// Two different connections matter and they fail differently:
//   `conn` — this browser's socket to the captions server.
//   `link` — the server's connection to the speech service, broadcast to
//            every tab and re-stated to each new one as it arrives.
// The hall only ever sees captions when BOTH are healthy, so a fault in
// either blanks the overlay. A frozen line is worse than an empty one: it
// reads as though the tool is working when it is not.
export type Link = {
  state: LinkState;
  attempt: number;
  reason: LinkReason;
  retryInSec: number | null;
};

const LINK_IDLE: Link = { state: "idle", attempt: 0, reason: null, retryInSec: null };

type State = {
  // Server-rendered boot config (branding + lang matrix + defaults).
  // Populated by applyConfig() right after /api/config returns, before
  // the React tree mounts. Held here so any component can read the
  // current accent/branding without re-fetching.
  appName:     string;
  sarvamLangs: Array<[string, string]>;
  mayuraLangs: string[];
  sarvamModels: Array<{ id: string; label: string }>;
  // In-browser debug log — fed by the WS `log_snapshot` (on connect)
  // and `log` (live) events. Bounded to MAX_DEBUG_LOGS so a chatty
  // process doesn't grow the store unbounded.
  debugLogs: LogRow[];
  // Connection / session
  conn: Connection;
  link: Link;
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
  // Live caption display — a rolling LINE scroll. See LINE_MIN_DWELL_MS.
  partialActive: boolean;
  // The lines the hall is reading, oldest FIRST, newest last.
  //
  // 🔴 The unit is a LINE, not a block. The block model replaced each caption
  // wholesale, so the eye finished the last line, the screen blanked and
  // refilled, and the reader started again with no thread back to what they
  // had just read. The owner asked for the thread: "2 on the screen and 3rd 1
  // is on the queue and 4th one is being constructed ... I want the text to
  // scroll up in a queue as the next line is generated".
  //
  // Bounded by `maxVisibleLines`: a new line enters at the END and the oldest
  // falls off the front, which is the scroll.
  visibleLines: string[];
  // Complete lines waiting their turn, oldest first.
  //
  // ⚠️ BOUNDED, and the bound is the lateness bound. One line queued is the
  // shape the owner asked for; `MAX_QUEUED_LINES` is the ceiling past which
  // the oldest queued line is dropped and counted. A queue allowed to grow is
  // the Day 2 Morning fault rebuilt deliberately — that was 45-60s behind the
  // speaker and getting worse.
  //
  // Each entry carries the caption it came from, because what gets dropped at
  // the bound is a whole sentence and never part of one. See `enqueueLines`.
  lineQueue: QueuedLine[];
  // Text that has arrived but does not yet fill a line.
  //
  // 🔴 A half-built line NEVER reaches the screen. That is the central rule of
  // this display: the sabha reads whole lines appearing, never words
  // assembling themselves. A line is complete when it fills the measured width
  // OR when the text building it ends a sentence — the owner settled the
  // second: "Push the short 1 up". Neither waits for the other.
  building: string;
  // Which caption the arriving text belongs to. Increments once per FINAL, and
  // exists only to group a sentence's lines together in the queue so the bound
  // can drop whole ones.
  captionSeq: number;
  // The caption whose lines are currently going up. Protected from the queue
  // bound: a sentence the hall has started reading is finished, because a
  // sentence that stops halfway is the fault this display exists to prevent,
  // arrived at from the other direction.
  deliveringCaptionId: number;
  // How many characters ONE line may hold. Only ever a FALLBACK — see
  // `splitCaption`. Character count is not a reliable unit for how much space
  // text takes: "Bhagvan Swaminarayan wrote the Shikshapatri" is far wider
  // than the same number of characters of short common words.
  lineCharBudget: number;
  // How arriving text is cut into single lines.
  //
  // Injected, because the only correct answer needs to LAY THE TEXT OUT, and
  // the store has no DOM. The renderer installs a version that measures the
  // real words in the real box; this default divides by a character budget and
  // is wrong for wide words, which is exactly the fault it replaced. Keeping
  // it as the default means the store stays testable headlessly and the first
  // caption of a session still gets a sane answer.
  splitCaption: (text: string) => string[];
  // How many lines the hall can see at once.
  //
  // Told by the renderer, which is the only thing that knows what the box can
  // actually hold — `settings.lines` is a preference and the box height wins.
  // Two knobs for one question is how the line count and the box height ended
  // up clipping text at each other.
  maxVisibleLines: number;
  lineShownAt: number;          // epoch ms the newest visible line entered
  // Gaps between the last few LINE arrivals, newest last. Measured rather
  // than assumed: the dwell floor is only safe RELATIVE to how fast lines
  // actually arrive, and that depends on the speaker, the language and the
  // sentence settings — not on a constant someone wrote down once.
  arrivalGaps: number[];
  // The last few lines that actually reached the screen, oldest first.
  //
  // 🔴 Not the transcript, which records captions as they ARRIVE. This records
  // what was DISPLAYED, which is a different thing the moment a caption is
  // split across lines or a queued one is dropped — and it is the only way to
  // answer "did the whole sentence appear, and in order?" from outside.
  //
  // The status board reports every 5s and lines change every second or two, so
  // a snapshot cannot answer that: it will always miss some of them. This is
  // the sequence rather than the snapshot.
  shownLines: Array<{ at: number; text: string }>;
  // How many completed lines the hall never saw, because speech outran the
  // display and the queue bound discarded them.
  //
  // Dropping is correct at the bound — queueing without limit is what put the
  // captions 45-60s behind on Day 2 Morning. But a drop that leaves no trace
  // means nobody can ever say how much of the discourse the sabha actually
  // read, and "it silently did something" is the disease that has already cost
  // this project a live fault and a mid-katha hour. So it is counted, and the
  // count is visible.
  linesDropped: number;
  lastFinalAt: number;          // epoch ms of the last FINAL, for the silence clear
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
  setLink: (l: Link) => void;
  setTab: (t: State["tab"]) => void;
  setRunning: (r: boolean) => void;
  setLangs: (s: string, t: string) => void;
  setAudioPeak: (p: number | null) => void;
  setAudioDevice: (d: string | undefined) => void;
  setAudioSource: (s: "device" | "file") => void;
  setAudioFile: (f: string, name?: string) => void;
  setLastSession: (srt: string | null, jsonl: string | null) => void;
  setPartialActive: (a: boolean) => void;
  pushFinal: (text: string, raw: string, rulesFired: string[], now?: number) => void;
  clearCaption: () => void;
  // Advance the caption clock: scroll one queued line onto the screen when the
  // newest line has served its time, release a line the speaker never finished,
  // then take the captions down after a silence. Called on an interval by the
  // renderer, and directly by tests with a clock they control. The thresholds
  // are parameters rather than module constants so a test does not have to
  // simulate twelve seconds to assert twelve seconds of behaviour.
  tickCaptionClock: (
    now: number, minDwellMs?: number, clearAfterMs?: number, orphanMs?: number,
  ) => void;
  // Told by the renderer once it has measured what actually fits.
  setLineCharBudget: (chars: number) => void;
  // Told by the renderer, which reconciles the operator's line preference with
  // what the caption box can actually hold.
  setMaxVisibleLines: (lines: number) => void;
  // Installed by the renderer, which can lay text out and therefore answer
  // exactly. Replaced with the character-budget fallback if it is ever unset.
  setLineSplitter: (split: ((text: string) => string[]) | null) => void;
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

// How long after the last finished caption before the screen is cleared.
//
// This is a judgement, not a measured value, and it is a hall-facing one:
// too short and a natural pause mid-sentence wipes text the congregation is
// still reading; too long and stale words sit over the picture. 12s is past
// any ordinary pause for breath and well short of the gap between passages.
// Overridable per deployment because a katha and a conference are not the
// same shape of speech.
// Settings are hydrated once, at module load, and BEFORE the constants below —
// they are derived from it, so they cannot drift from what the app runs on.
// Declared here rather than further down because a `const` referenced before
// its declaration throws at import, which would take the overlay with it.
const DEFAULT_SETTINGS = loadSettings();

// 🔴 Derived from the SETTING, not read from the URL a second time.
//
// `loadSettings()` already reads `clearafter` (and its newer name `maxdwell`)
// into `captionMaxDwellSec`. Parsing the same parameter again here made two
// knobs for one question, which is exactly what `captionMaxDwellSec` exists to
// prevent — and they could disagree, because only one of them honours
// `maxdwell` or a value the operator set in the toolbar.
//
// This constant survives only as the default for headless callers and tests;
// anything with settings in hand should use `settings.captionMaxDwellSec`.
export const CAPTION_CLEAR_AFTER_MS = Math.round(DEFAULT_SETTINGS.captionMaxDwellSec * 1000);

// How long the newest line must stay put before the next one may scroll in.
//
// This is what stops the scroll outrunning the reader. A line is COMPLETE
// before it is ever shown and it never mutates on screen, so the smoothness
// comes from a steady cadence of whole lines rather than from animating text
// into an incomplete one.
//
// ⚠️ This floor MUST stay below the LINE arrival interval. Lines arrive faster
// than captions did — one sentence is often two lines — so the old 2.0s block
// floor is NOT the right number here: two lines of a 3.8s sentence would need
// 4.0s and the screen would fall behind by the difference on every sentence.
// That is the Day 2 Morning fault rebuilt on purpose. `arrivalGaps` measures
// the real per-line interval and the toolbar warns when this gets close to it.
//
// 1.5s is the broadcast minimum for a line and sits comfortably under the
// ~1.9s per-line interval measured on Day 3 (3.83s median between captions,
// commonly two lines each). Settling it against the recorded katha audio is
// #50's job, not a number to nudge by feel.
export const LINE_MIN_DWELL_MS = 1500;

// How long text that has not filled a line waits before it is pushed up anyway.
//
// A line normally completes because it filled the measured width or because
// the sentence ended. Neither happens when the server releases a caption on
// its max-wait ceiling mid-sentence and the speaker then stops: the remainder
// would sit in `building` and the sabha would never read the last thing said.
//
// Sitting just above the server's 2.5s SENTENCE_QUIET_SEC means an ordinary
// gap between segments of one continuous sentence does not trip it, while an
// actual pause does.
//
// ⚠️ Without this the display has the same shape of fault as the old silence
// clear: a rule that could only fire once the thing it was waiting for had
// already happened.
export const LINE_ORPHAN_MS = 2600;

// The CEILING on lines waiting for the screen, not the working depth.
//
// 🔴 Read as a target this number looks wrong, and it has been read that way:
// the owner's shape is one queued line — "3rd 1 is on the queue" — and spec
// #56 says so. One IS the working depth; the queue sits at one line almost
// always. Four is the point at which the tool stops keeping up and starts
// dropping, which the same paragraph of #56 asks for in the same breath:
// "The pipeline may hold more only up to an explicit maximum; beyond it the
// oldest queued line is dropped and counted."
//
// Why there is any headroom at all: a burst of fast short sentences puts three
// or four lines in hand and they drain within a few seconds at the dwell
// floor. Past this the OLDEST queued line is dropped and counted, which bounds
// how far behind the voice the screen can ever be at roughly MAX_QUEUED_LINES
// x the dwell floor — six seconds, not a minute.
export const MAX_QUEUED_LINES = 4;

// How many recent arrival gaps the interval estimate is taken over. Twenty is
// about eighty seconds of speech at the observed cadence — long enough not to
// swing on one long sentence, short enough to follow a speaker changing pace.
const ARRIVAL_GAP_WINDOW = 20;

// How many displayed lines to keep. Enough to hold several whole sentences at
// the observed cadence — long enough to reassemble one and see the ones either
// side of it, short enough not to grow across a katha.
const SHOWN_LINE_WINDOW = 12;

/** Append a line to the displayed record, bounded. */
function recordShown(shown: State["shownLines"], text: string, at: number) {
  return [...shown, { at, text }].slice(-SHOWN_LINE_WINDOW);
}

// Punctuation that ends a unit of speech, in both scripts the tool handles.
//
// A caption ending here is finished, so whatever it leaves part-way through a
// line is complete too and goes up short — the owner's decision, 2026-09-03:
// "Push the short 1 up". A caption released by the server's max-wait ceiling
// mid-sentence ends without one, and its remainder keeps building instead.
const ENDS_SENTENCE = /[.!?।॥]["')\]]*\s*$/;

/**
 * A line waiting for the screen, tagged with the caption it came from.
 *
 * The tag exists for one reason: what gets dropped at the bound is a WHOLE
 * SENTENCE, never part of one. See `enqueueLines`.
 */
type QueuedLine = { text: string; captionId: number };

/**
 * Add complete lines to the queue, enforcing the bound.
 *
 * Returns the kept queue and how many lines were dropped to keep it.
 *
 * 🔴 IT DROPS WHOLE SENTENCES, OLDEST FIRST — never the first line of one.
 *
 * The obvious implementation trims the front of the line queue, and it is
 * wrong in the worst available way. The server caps a caption at 180
 * characters and a line holds about fifty, so ONE long sentence can be four
 * lines; trimming the front of the queue would put that sentence on the hall
 * screen starting from its second line. The sabha would read a confident,
 * fluent sentence that begins in the middle and has no marker saying so. That
 * exact fault has already shipped once here — a caption whose front was cut,
 * read out starting from "own hand, and set down in it..." — and a missing
 * sentence is far better than a beheaded one.
 *
 * Two sentences are never dropped:
 *   - the one that has just arrived, so the screen always has the newest
 *     speech (dropping THAT would be a queue that only ever plays catch-up);
 *   - the one already part-way onto the screen, because the hall is mid-read
 *     and a sentence the reader has started is finished.
 */
function enqueueLines(
  queue: QueuedLine[],
  lines: QueuedLine[],
  arrivingCaptionId: number,
  deliveringCaptionId: number,
): { queue: QueuedLine[]; dropped: number } {
  const all = [...queue, ...lines];
  if (all.length <= MAX_QUEUED_LINES) return { queue: all, dropped: 0 };

  let kept = all;
  while (kept.length > MAX_QUEUED_LINES) {
    const victim = kept.find(
      (l) => l.captionId !== arrivingCaptionId && l.captionId !== deliveringCaptionId,
    );
    // Nothing left that may be dropped: the queue is one protected sentence,
    // which is bounded by the server's own 180-character ceiling and drains
    // in seconds.
    if (!victim) break;
    kept = kept.filter((l) => l.captionId !== victim.captionId);
  }
  return { queue: kept, dropped: all.length - kept.length };
}

// Everything the hall can read, emptied.
//
// The queue and the line under construction go with the visible lines. A line
// held back belongs to speech that is no longer current; putting it up after
// the screen has deliberately been emptied is how a stale sentence returns to
// the hall screen on its own.
const BLANK_SCREEN = {
  visibleLines: [] as string[], lineQueue: [] as QueuedLine[], building: "", deliveringCaptionId: 0,
};

const MAX_CAPTION_LEN = 6000;
const MAX_TRANSCRIPT  = 500;
const MAX_DEBUG_LOGS  = 400;     // matches server-side _recent_logs deque size

// Initial settings come from URL params (overlay) or localStorage, with
// defaults falling back. Loaded once at module init.
const _initialSettings = DEFAULT_SETTINGS;

// The initializer is named and exported so a test can build a FRESH store
// per case. Reaching for the `useStore` singleton instead would let one
// test's captions leak into the next, and a caption that survives when it
// should not is the exact class of fault this suite exists to catch.
export const captionStore: StateCreator<State> = (set, get) => ({
  appName:     "Captions",
  sarvamLangs: [],
  mayuraLangs: [],
  sarvamModels: [],
  debugLogs:   [],
  conn: "connecting",
  link: LINK_IDLE,
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
  visibleLines: [],
  lineQueue: [],
  building: "",
  captionSeq: 0,
  deliveringCaptionId: 0,
  maxVisibleLines: DEFAULTS.lines,
  // One LINE's worth, not a block's: the fallback budget has to describe the
  // same unit the splitter produces or the first caption of a session arrives
  // as one over-long line.
  lineCharBudget: estimateCharsPerLine(DEFAULTS.areaW, DEFAULTS.fontSize),
  splitCaption: (text) => splitIntoLines(text, get().lineCharBudget),
  lineShownAt: 0,
  arrivalGaps: [],
  shownLines: [],
  linesDropped: 0,
  lastFinalAt: 0,
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
      let settings = hasStored
        ? st.settings
        : { ...st.settings, source: cfg.defaultSource, target: cfg.defaultTarget };
      let migrated = !hasStored;

      // A model that was valid when it was stored may since have been
      // withdrawn. The server already refuses to use one it doesn't
      // recognise, so captions are never at risk — but the <select> would
      // render BLANK, because its value matches no option. An operator
      // starting a katha would see an empty dropdown, have no idea what is
      // actually running, and no way to tell whether that was the fault.
      // Migrate the stored value to the current default instead.
      const known = new Set(cfg.sarvamModels.map((m) => m.id));
      if (cfg.sarvamModels.length && !known.has(settings.sarvamModel)) {
        settings = { ...settings, sarvamModel: cfg.sarvamModels[0].id };
        migrated = true;
      }
      if (migrated) saveSettings(settings);
      return {
        appName:      cfg.appName,
        sarvamLangs:  cfg.sarvamLangs,
        mayuraLangs:  cfg.mayuraLangs,
        sarvamModels: cfg.sarvamModels,
        langSource:   settings.source,
        langTarget:   settings.target,
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
  setConn: (c) => set((st) => {
    // Losing our own socket means we can no longer know whether captions are
    // flowing, so the honest thing to show is nothing. Only a confirmed close
    // counts — "connecting" is also the state on first load, and a fresh page
    // must not open with a fault on it. The server re-states the real link
    // state the moment the socket is back (see handle_ws).
    if (c !== "closed") return { conn: c };
    return {
      conn: c,
      link: { state: "disconnected", attempt: st.link.attempt,
              reason: null, retryInSec: null },
      ...BLANK_SCREEN,
      partialActive: false,
    };
  }),
  setLink: (l) => set(() => {
    // Anything but a live link means whatever is on screen is no longer being
    // spoken. The server sends `clear` for the same reason — this covers the
    // tab that learns about an outage from its arrival snapshot instead.
    if (l.state === "connected") return { link: l };
    return { link: l, ...BLANK_SCREEN, partialActive: false };
  }),
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
  pushFinal: (text, raw, rulesFired, now = Date.now()) => set((st) => {
    // A ROLLING SCROLL OF WHOLE LINES.
    //
    // The owner settled this display model on Day 3, after watching the block
    // model in the hall: "2 on the screen and 3rd 1 is on the queue and 4th
    // one is being constructed ... I want the text to scroll up in a queue as
    // the next line is generated. Only full line should be pushed upwards".
    //
    // So an arrival never goes straight to the screen. It is appended to
    // whatever line is still being built, the splitter peels off every line
    // that is now COMPLETE, and those wait their turn. The heartbeat scrolls
    // them on one at a time.
    //
    // What this replaced: a one-deep pipeline of two-line BLOCKS, each
    // replacing the last wholesale. The sabha read islands — the eye finished
    // line two, the screen blanked and refilled, and nothing carried over to a
    // discourse where one sentence leans on the one before it. It also dropped
    // 24 of 690 sentences on Day 3 (3.5%), because one caption in reserve has
    // nowhere to put a burst.
    //
    // ⚠️ THE QUEUE IS BOUNDED AND THAT BOUND IS THE SAFETY PROPERTY. Past
    // MAX_QUEUED_LINES the oldest queued line is dropped and counted. A buffer
    // allowed to grow is the Day 2 Morning fault rebuilt deliberately — that
    // was 45-60s behind the speaker and getting worse.
    let incoming = text;
    if (incoming.length > MAX_CAPTION_LEN) incoming = incoming.slice(-MAX_CAPTION_LEN);

    const transcript = [...st.transcript, { ts: new Date().toISOString(), text, raw, rulesFired }];
    if (transcript.length > MAX_TRANSCRIPT) transcript.splice(0, transcript.length - MAX_TRANSCRIPT);

    // Continue the line that was still being built rather than starting a new
    // one. This is what stops a sentence boundary being a screen boundary.
    const source = st.building ? `${st.building} ${incoming}`.trim() : incoming.trim();

    // 🔴 Cut where the text ACTUALLY stops fitting, one line at a time.
    //
    // The store's own splitter divides by a character budget and is wrong for
    // wide words; the renderer installs one that lays the real words out in
    // the real box. Three attempts to compute this arithmetically all passed
    // their tests and all failed on the screen. See docs/how-it-works.md.
    const parts = source ? st.splitCaption(source) : [];

    // A caption that ends a sentence is finished, so its last part is complete
    // even if it is short — "Push the short 1 up", the owner, 2026-09-03. One
    // released mid-sentence on the server's max-wait ceiling has not finished,
    // so its remainder keeps building and the next arrival continues it.
    const finished = ENDS_SENTENCE.test(incoming);
    const complete = finished ? parts : parts.slice(0, -1);
    const building = finished ? "" : (parts[parts.length - 1] ?? "");

    // One caption, one id: its lines stay together in the queue so the bound
    // can drop the whole sentence rather than behead it.
    const captionId = st.captionSeq + 1;
    const { queue, dropped } = enqueueLines(
      st.lineQueue, complete.map((text) => ({ text, captionId })), captionId, st.deliveringCaptionId,
    );

    // Record the gap since the previous arrival, as a PER-LINE interval: one
    // caption commonly yields two lines, and it is lines that now have to fit
    // through the dwell floor. Measuring captions here would overstate the
    // interval by the number of lines they carry, which is the dangerous
    // direction — it makes an unsafe floor look safe.
    //
    // Gaps longer than the silence threshold are pauses between passages, not
    // the speaker's cadence, and would drag the estimate upwards too.
    const gap = st.lastFinalAt ? now - st.lastFinalAt : 0;
    const silenceMs = (st.settings.captionMaxDwellSec || 0) * 1000 || CAPTION_CLEAR_AFTER_MS;
    const arrivalGaps = gap > 0 && gap <= silenceMs && complete.length > 0
      ? [...st.arrivalGaps, ...complete.map(() => Math.round(gap / complete.length))]
          .slice(-ARRIVAL_GAP_WINDOW)
      : st.arrivalGaps;

    return {
      lineQueue: queue,
      building,
      captionSeq: captionId,
      linesDropped: st.linesDropped + dropped,
      transcript,
      arrivalGaps,
      lastFinalAt: now,
    };
  }),
  // Blank everything, including what was queued and what was still building.
  // A line held back belongs to speech that has now finished; scrolling it on
  // after a `clear` would put a stale sentence back on the hall screen after
  // the screen had been deliberately emptied.
  clearCaption: () => set({ ...BLANK_SCREEN, lineShownAt: 0, lastFinalAt: 0 }),
  setLineCharBudget: (chars) => set({ lineCharBudget: Math.max(1, Math.floor(chars)) }),
  setMaxVisibleLines: (lines) => set((st) => {
    const n = Math.max(1, Math.floor(lines));
    // Trim what is already up, so shrinking the count cannot leave a line
    // sitting outside the panel with `overflow: hidden` cutting it in half.
    return { maxVisibleLines: n, visibleLines: st.visibleLines.slice(-n) };
  }),
  setLineSplitter: (split) => set({
    splitCaption: split ?? ((text: string) => splitIntoLines(text, get().lineCharBudget)),
  }),
  // Scroll the screen on, and take it down once the speaker has stopped.
  //
  // The DECISION lives here rather than in the renderer so it can be
  // asserted without a browser. The renderer supplies the heartbeat; this
  // supplies the rule. Previously both lived in a useEffect, which is why
  // the one behaviour that had already failed in front of the hall was the
  // one behaviour nothing could test.
  tickCaptionClock: (
    now,
    minDwellMs = LINE_MIN_DWELL_MS,
    clearAfterMs = CAPTION_CLEAR_AFTER_MS,
    orphanMs = LINE_ORPHAN_MS,
  ) => {
    const st = get();

    // 1. Release a line the speaker never finished.
    //
    // Text sits in `building` when a caption arrived without ending a
    // sentence — the server released it on its max-wait ceiling mid-flow. If
    // the speaker then stops, nothing else will ever complete that line, and
    // the last thing he said would never reach the sabha.
    if (st.building && st.lastFinalAt && now - st.lastFinalAt >= orphanMs) {
      const { queue, dropped } = enqueueLines(
        st.lineQueue,
        [{ text: st.building, captionId: st.captionSeq }],
        st.captionSeq,
        st.deliveringCaptionId,
      );
      set({ lineQueue: queue, building: "", linesDropped: st.linesDropped + dropped });
      return;
    }

    // 2. Scroll ONE complete line on.
    //
    // One at a time, so the reader's eye can follow where the line it was
    // reading went. The newest line has to have served its minimum dwell
    // first: that floor is what stops the scroll outrunning the hall.
    //
    // 🔴 Only complete lines are ever here — `building` is not a candidate.
    // Never push a half-built line up: the owner's rule, and the one this
    // display exists to keep.
    const screenIsFree = st.visibleLines.length === 0 || now - st.lineShownAt >= minDwellMs;
    if (st.lineQueue.length > 0 && screenIsFree) {
      const next = st.lineQueue[0];
      set({
        // The sentence now going up is protected from the queue bound until
        // its last line has gone up.
        deliveringCaptionId: next.captionId,
        // A line enters at the BOTTOM and the oldest falls off the top. That
        // is the scroll, and it is why the previous line is still in front of
        // the reader while the new one arrives.
        visibleLines: [...st.visibleLines, next.text].slice(-Math.max(1, st.maxVisibleLines)),
        lineQueue: st.lineQueue.slice(1),
        lineShownAt: now,
        shownLines: recordShown(st.shownLines, next.text, now),
      });
      return;
    }

    // 3. Take the captions down once the speaker has stopped.
    //
    // Measured from the last ARRIVAL, not from when a line went up: the
    // question is how long the speaker has been silent, not how long the
    // screen has been showing something.
    //
    // `lastFinalAt` of 0 means nothing has arrived, so there is nothing to
    // take down and no clock to run against.
    if (!st.lastFinalAt) return;
    if (now - st.lastFinalAt > clearAfterMs) get().clearCaption();
  },
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
});

/** The app-wide store. Tests use `create(captionStore)` for an isolated one. */
export const useStore = create<State>(captionStore);

