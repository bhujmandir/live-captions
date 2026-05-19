import type { Feed, Rule, VodJob, VodRange, AudioDevice, WsMsg, SessionMeta, SessionDetail, AppConfig } from "./types";
import { useStore } from "./store";

// Single WebSocket, reconnects automatically on close.

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export function connectWs() {
  const { setConn } = useStore.getState();
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  setConn("connecting");
  ws.onopen = () => useStore.getState().setConn("open");
  ws.onclose = () => {
    useStore.getState().setConn("closed");
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectWs, 1500);
  };
  ws.onerror = () => { /* onclose will fire */ };
  ws.onmessage = (evt) => {
    let msg: WsMsg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    handleWsMessage(msg);
  };
}

function handleWsMessage(msg: WsMsg) {
  const st = useStore.getState();
  switch (msg.type) {
    case "session_status":
      st.setRunning(msg.running);
      st.setLangs(msg.lang_source, msg.lang_target);
      if (msg.device) st.setAudioDevice(msg.device);
      break;
    case "final":
      st.pushFinal(msg.text, msg.raw || "", msg.rules_fired || []);
      break;
    case "partial":
      st.setPartialActive(!!(msg.text && msg.text.trim()));
      break;
    case "level":
      st.setAudioPeak(msg.peak);
      break;
    case "stopped":
      st.setRunning(false);
      st.setAudioPeak(null);
      break;
    case "clear":
      st.clearCaption();
      st.setPartialActive(false);
      break;
    case "reconnected":
      // Legacy UI also handled this — could surface a brief chip if we
      // wanted. For now just ignore (status pill already shows "open").
      break;
    case "feeds_list":
      st.setFeeds(msg.feeds || []);
      break;
    case "rules_list":
      st.setRules(msg.rules || []);
      break;
    case "session_saved":
      // Stash the static-mount URLs so the Live tab can surface a
      // Download SRT button once a file-mode session finishes. Falls
      // back to deriving `/results/<name>` from the raw path for older
      // servers that don't yet send the *_url fields.
      {
        const srtUrl   = msg.srt_url   ?? (msg.srt   ? `/results/${msg.srt.split("/").pop()}`   : null);
        const jsonlUrl = msg.jsonl_url ?? (msg.jsonl ? `/results/${msg.jsonl.split("/").pop()}` : null);
        st.setLastSession(srtUrl, jsonlUrl);
      }
      break;
    case "vod_jobs_list":
      st.setVodJobs(msg.jobs || []);
      break;
    case "vod_job":
      st.upsertVodJob(msg.job);
      break;
    case "vod_job_deleted":
      st.removeVodJob(msg.id);
      break;
    case "log_snapshot":
      st.setLogSnapshot(msg.logs || []);
      break;
    case "log":
      st.pushLog(msg.record);
      break;
  }
}

// ── REST helpers ────────────────────────────────────────────────────

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function jpost<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function jdelete<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Devices + capture ──────────────────────────────────────────────
export const api = {
  // Boot config — fetched once before React mounts (see main.tsx). Held
  // in the store so any component can read appName / sarvamLangs etc.
  getConfig:     () => jget<AppConfig>("/api/config"),

  listDevices:   () => jget<{ devices: AudioDevice[] }>("/api/devices"),
  startCapture:  (body: any) => jpost("/api/start", body),
  stopCapture:   () => jpost("/api/stop"),
  setDirection:  (source: string, target: string) =>
                    jpost("/api/direction", { source, target }),

  // Outputs (YouTube CC feeds)
  listFeeds:     () => jget<{ feeds: Feed[] }>("/api/feeds"),
  saveFeed:      (body: Partial<Feed> & { stream_key?: string }) => jpost("/api/feeds", body),
  enableFeed:    (id: string) => jpost(`/api/feeds/${encodeURIComponent(id)}/enable`),
  disableFeed:   (id: string) => jpost(`/api/feeds/${encodeURIComponent(id)}/disable`),
  deleteFeed:    (id: string) => jdelete(`/api/feeds/${encodeURIComponent(id)}`),
  enableAllFeeds:  () => jpost("/api/feeds/enable-all"),
  disableAllFeeds: () => jpost("/api/feeds/disable-all"),

  // Rules
  listRules:     () => jget<{ rules: Rule[] }>("/api/rules"),
  saveRule:      (body: Partial<Rule>) => jpost("/api/rules", body),
  deleteRule:    (id: string) => jdelete(`/api/rules/${encodeURIComponent(id)}`),

  // VOD jobs
  listVodJobs:   () => jget<{ jobs: VodJob[] }>("/api/vod-jobs"),
  createVodJob:  (video_url: string) => jpost<{ ok: boolean; job: VodJob }>("/api/vod-jobs", { video_url }),
  cancelVodJob:    (id: string) => jpost(`/api/vod-jobs/${encodeURIComponent(id)}/cancel`),
  // Resume an awaiting_ranges job with operator-picked sections.
  // Empty array = transcribe the full video.
  transcribeVodJob:(id: string, ranges: VodRange[]) =>
                    jpost(`/api/vod-jobs/${encodeURIComponent(id)}/transcribe`, { ranges }),
  deleteVodJob:    (id: string) => jdelete(`/api/vod-jobs/${encodeURIComponent(id)}`),

  // Test render — server broadcasts a synthetic FINAL so every connected
  // tab (operator + overlay) renders the same text.
  testRender:    (text?: string) => jpost("/api/test-render", text ? { text } : {}),

  // Audio upload — multipart POST. Returns the absolute server-side path
  // that should then be passed as `file` to /api/start with source=file.
  uploadAudio: async (file: File): Promise<{ ok: boolean; path: string; name: string; size: number }> => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const r = await fetch("/api/upload-audio", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },

  // Past sessions browser
  listSessions:   () => jget<{ sessions: SessionMeta[] }>("/api/sessions"),
  getSession:     (id: string) => jget<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  deleteSession:  (id: string) => jdelete(`/api/sessions/${encodeURIComponent(id)}`),
};
