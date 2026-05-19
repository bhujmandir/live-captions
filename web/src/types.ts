// Wire-level types — keep in sync with live_captions.py's WS message shapes.

export type Direction = { source: string; target: string };

export type Feed = {
  id: string;
  label: string;
  stream_key_tail: string;
  target_lang: string;
  enabled: boolean;
  advance_sec: number;
  configured: boolean;
  sent: number;
  errors: number;
  seq?: number;
  queue_size?: number;
  last_sent_at?: number;
  last_error?: string;
};

export type Rule = {
  id: string;
  pattern: string;
  replacement: string;
  regex: boolean;
  enabled: boolean;
  is_exclusion: boolean;
  error: string;
};

export type AudioDevice = {
  id: number;
  name: string;
  channels: number;
  default: boolean;
};

export type VodRange = { start_s: number; end_s: number };

export type VodJob = {
  id: string;
  video_url: string;
  video_id: string;
  status: "queued" | "running" | "awaiting_ranges" | "done" | "failed" | "cancelled";
  stage: string;
  stage_label: string;
  stage_pct: number | null;
  stage_detail: string;
  stage_elapsed_s: number;
  created_at: string;
  started_at: string;
  finished_at: string;
  error: string;
  ranges: VodRange[];
  cue_count: number;
  rules_fired_count: number;
  srt_url: string;
  preview_url: string;
  mp4_url: string;
};

export type TranscriptEntry = {
  ts: string;             // local ISO when received in browser
  text: string;
  raw: string;
  rulesFired: string[];
};

// Past-session metadata as exposed by GET /api/sessions. Mirrors the
// SessionRecorder header + footer; jsonl_url / srt_url are static
// /results/ paths the React UI can link directly.
export type SessionMeta = {
  id:           string;       // filename without extension
  started_at:   string;
  ended_at:     string;
  source_lang:  string;
  target_lang:  string;
  audio_source: string;
  device:       string | null;
  file:         string | null;
  final_count:  number;
  elapsed_s:    number;
  jsonl_url:    string;
  srt_url:      string | null;
  active:       boolean;
  size_bytes:   number;
};

// Single past-session detail: header + the final captions as parsed
// from the JSONL on the server. Same shape the live transcript uses.
export type SessionDetail = {
  session: SessionMeta;
  finals: Array<{
    ts:          string;
    elapsed_s:   number;
    raw:         string;
    text:        string;
    rules_fired: string[];
    audio_level: number | null;
  }>;
};

// Server-rendered boot config (GET /api/config). Fetched once before the
// React tree mounts so we can set document.title, write the --accent CSS
// var, and populate the lang dropdowns from a single source of truth.
export type AppConfig = {
  appName:       string;
  accentHsl:     string;          // HSL channels, e.g. "28 100% 55%"
  defaultSource: string;
  defaultTarget: string;
  sarvamLangs:   Array<[string, string]>;
  mayuraLangs:   string[];
};

// One log record in the debug ring buffer.
export type LogRow = {
  t:      number;
  level:  string;
  logger: string;
  msg:    string;
};

// Union of every type:... WS frame the backend can emit.
export type WsMsg =
  | { type: "session_status"; running: boolean; lang_source: string; lang_target: string;
       audio_source?: string; device?: string; file?: string; }
  | { type: "final";   text: string; raw: string; rules_fired: string[]; target_lang: string }
  | { type: "partial"; text: string }
  | { type: "level";   peak: number }
  | { type: "stopped" }
  | { type: "clear" }
  | { type: "reconnected"; attempt: number }
  | { type: "feeds_list"; feeds: Feed[] }
  | { type: "rules_list"; rules: Rule[] }
  | { type: "session_saved"; jsonl?: string; srt?: string; jsonl_url?: string | null; srt_url?: string | null }
  | { type: "vod_jobs_list"; jobs: VodJob[] }
  | { type: "vod_job"; job: VodJob }
  | { type: "vod_job_deleted"; id: string }
  | { type: "log_snapshot"; logs: LogRow[] }
  | { type: "log";          record: LogRow }
  ;
