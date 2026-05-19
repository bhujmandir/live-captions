import { useEffect, useMemo, useState } from "react";
import { useStore } from "@/store";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { SessionMeta, SessionDetail } from "@/types";
import { timeAgo, durationStr, cn } from "@/lib/utils";
import {
  ScrollText, Copy, Trash2, Radio, Download, RefreshCw, Loader2, AlertCircle,
} from "lucide-react";

// Session-browsing transcript view.
//
// Left: list of sessions on disk, newest-first, with a synthetic
//   "Live" entry pinned at the top while a session is actively
//   running (in-memory captions from the WS feed).
// Right: the selected session's captions in the same render shape the
//   live transcript uses. Past sessions also get a "Download SRT" link
//   when SessionRecorder's sibling .srt file is present.
//
// On WS-driven state changes (a new FINAL arriving, or the active
// session flipping start/stop), we transparently re-fetch the session
// list so the operator's left rail stays accurate.

export function TranscriptTab() {
  const running        = useStore((s) => s.running);
  const liveTranscript = useStore((s) => s.transcript);
  const clearLive      = useStore((s) => s.clearTranscript);

  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // null while loading the past session; set once parsed.
  const [pastDetail, setPastDetail] = useState<SessionDetail | null>(null);
  const [pastLoading, setPastLoading] = useState(false);
  const [pastErr, setPastErr] = useState<string>("");

  // Refresh session list on mount, when running changes (so "Live"
  // pill toggles), and when new FINALs land (counts on disk update).
  useEffect(() => {
    let cancelled = false;
    const fetchList = () => {
      api.listSessions().then((d) => {
        if (cancelled) return;
        setSessions(d.sessions || []);
        // Default selection: live if running, else newest past session.
        setSelectedId((prev) => {
          if (prev) return prev;
          if (running) return "__live__";
          const first = (d.sessions || [])[0];
          return first ? first.id : "__live__";
        });
      }).catch(() => {});
    };
    fetchList();
    return () => { cancelled = true; };
    // Re-fetch list on running flip + every 5s while running (file
    // size + final_count tick along). When idle we don't poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  // Light polling while a session is recording so file size + count
  // keep up. Stops as soon as the session ends.
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => {
      api.listSessions().then((d) => setSessions(d.sessions || [])).catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [running]);

  // Whenever the selection changes to a past session, fetch its
  // detail. The live one renders from store state so no fetch needed.
  useEffect(() => {
    if (!selectedId || selectedId === "__live__") {
      setPastDetail(null); setPastErr("");
      return;
    }
    let cancelled = false;
    setPastLoading(true); setPastErr("");
    api.getSession(selectedId).then((d) => {
      if (cancelled) return;
      setPastDetail(d);
    }).catch((e) => {
      if (cancelled) return;
      setPastErr(String(e?.message || e));
    }).finally(() => {
      if (!cancelled) setPastLoading(false);
    });
    return () => { cancelled = true; };
  }, [selectedId]);

  const liveActive = running;
  const showLive   = selectedId === "__live__";

  // The rows we render in the right pane.
  const rows = useMemo(() => {
    if (showLive) {
      return liveTranscript.map((e) => ({
        ts: e.ts, text: e.text, raw: e.raw, rulesFired: e.rulesFired,
      }));
    }
    if (!pastDetail) return [];
    return pastDetail.finals.map((f) => ({
      ts: f.ts, text: f.text, raw: f.raw, rulesFired: f.rules_fired,
    }));
  }, [showLive, liveTranscript, pastDetail]);

  const copy = async () => {
    const txt = rows.map((e) => {
      const ts = new Date(e.ts).toLocaleTimeString("en-GB", { hour12: false });
      return `[${ts}] ${e.text}`;
    }).join("\n");
    try { await navigator.clipboard.writeText(txt); } catch {}
  };

  const refresh = () => api.listSessions().then((d) => setSessions(d.sessions || [])).catch(() => {});

  const selectedMeta = sessions.find((s) => s.id === selectedId);

  return (
    <div className="h-full grid grid-cols-[18rem_1fr] min-h-0">
      {/* ── Left: session list ───────────────────────────────────── */}
      <aside className="border-r border-border bg-surface/40 flex flex-col min-h-0">
        <header className="flex items-center justify-between px-3 py-2 border-b border-border">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-accent" />
            <h2 className="text-xs font-semibold uppercase tracking-wider">Sessions</h2>
            <Badge>{sessions.length + (liveActive ? 1 : 0)}</Badge>
          </div>
          <Button variant="ghost" size="icon" onClick={refresh} title="Refresh list">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </header>
        <ul className="flex-1 overflow-y-auto divide-y divide-border">
          {/* Live entry — always at top, only shown while a session is
              actively recording. */}
          {liveActive && (
            <SessionRow
              isLive
              active={showLive}
              onClick={() => setSelectedId("__live__")}
              title="Live (recording now)"
              meta={liveSessionPseudoMeta(liveTranscript.length)}
            />
          )}
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              meta={s}
              active={selectedId === s.id}
              onClick={() => setSelectedId(s.id)}
              onDelete={async () => {
                if (!confirm(`Delete session ${s.id}? Removes JSONL + SRT from disk.`)) return;
                try {
                  await api.deleteSession(s.id);
                  refresh();
                  if (selectedId === s.id) setSelectedId(liveActive ? "__live__" : null);
                } catch (e: any) {
                  alert("Delete failed: " + (e?.message || e));
                }
              }}
            />
          ))}
          {sessions.length === 0 && !liveActive && (
            <li className="p-4 text-xs text-fgMuted italic">
              No sessions recorded yet. Start a capture and the session JSONL + SRT will appear here.
            </li>
          )}
        </ul>
      </aside>

      {/* ── Right: selected session detail ───────────────────────── */}
      <section className="flex flex-col min-h-0">
        <header className="flex items-center justify-between border-b border-border bg-surface px-4 py-2.5">
          <div className="flex items-center gap-3 min-w-0">
            {showLive ? (
              <>
                <Radio className="h-4 w-4 text-accent animate-pulse" />
                <h2 className="text-sm font-semibold">Live transcript</h2>
                <Badge variant="success">recording</Badge>
              </>
            ) : selectedMeta ? (
              <>
                <ScrollText className="h-4 w-4 text-fgMuted" />
                <h2 className="text-sm font-semibold truncate" title={selectedMeta.id}>
                  {sessionLabel(selectedMeta)}
                </h2>
                <Badge>{selectedMeta.final_count} captions</Badge>
                {selectedMeta.elapsed_s > 0 && (
                  <span className="text-[10px] text-fgMuted font-mono">
                    {durationStr(selectedMeta.elapsed_s)}
                  </span>
                )}
              </>
            ) : (
              <h2 className="text-sm text-fgMuted">No session selected</h2>
            )}
          </div>
          <div className="flex gap-2">
            {!showLive && selectedMeta?.srt_url && (
              <Button asChild variant="ghost" size="sm" title="Download .srt file">
                <a href={selectedMeta.srt_url} download>
                  <Download className="h-4 w-4" /> SRT
                </a>
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={copy} disabled={!rows.length}>
              <Copy className="h-4 w-4" /> Copy
            </Button>
            {showLive && (
              <Button
                variant="ghost" size="sm"
                onClick={() => {
                  if (rows.length && !confirm("Clear the live transcript log?")) return;
                  clearLive();
                }}
                disabled={!rows.length}
              >
                <Trash2 className="h-4 w-4" /> Clear
              </Button>
            )}
          </div>
        </header>

        <div className="overflow-y-auto p-4 flex-1">
          {pastErr && (
            <div className="max-w-3xl mx-auto mb-3 flex items-start gap-2 rounded border border-danger/40 bg-danger/10 p-2.5 text-sm text-danger">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /> <span>{pastErr}</span>
            </div>
          )}
          {!showLive && pastLoading && (
            <div className="text-center text-fgMuted py-12 flex items-center justify-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading session…
            </div>
          )}
          {!pastLoading && rows.length === 0 && (
            <div className="text-center text-fgMuted py-12">
              {showLive
                ? "No captions yet. Once a session is running, FINAL captions appear here in real time."
                : selectedMeta
                  ? "Session has no FINAL captions recorded."
                  : "Pick a session from the list on the left."}
            </div>
          )}
          {rows.length > 0 && (
            <div className="max-w-3xl mx-auto space-y-1.5">
              {/* Newest first — operator never has to scroll for the
                  latest. Copy-to-clipboard still emits chronological
                  order from `rows`, which we don't mutate. */}
              {rows.slice().reverse().map((e, idx) => {
                const i = rows.length - 1 - idx;
                const ts = new Date(e.ts).toLocaleTimeString("en-GB", { hour12: false });
                const hasRules = e.rulesFired.length > 0 && e.raw && e.raw !== e.text;
                return (
                  <div key={i} className="flex gap-3 text-sm leading-relaxed">
                    <span className="font-mono text-[11px] text-fgMuted shrink-0 mt-0.5 w-16">{ts}</span>
                    <span className="flex-1">
                      {e.text}
                      {hasRules && (
                        <span
                          title={`Pre-rules text:\n${e.raw}\n\nRules fired:\n• ${e.rulesFired.join("\n• ")}`}
                          className="ml-2 inline-flex items-center px-1.5 rounded bg-warn/20 text-warn font-mono text-[10px] cursor-help align-middle border border-warn/30"
                        >
                          rules · {e.rulesFired.length}
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────

function SessionRow({
  meta, active, onClick, onDelete, isLive, title,
}: {
  meta: SessionMeta;
  active: boolean;
  onClick: () => void;
  onDelete?: () => void;
  isLive?: boolean;
  title?: string;
}) {
  return (
    <li>
      <div
        onClick={onClick}
        className={cn(
          "group flex items-start gap-2 p-2.5 cursor-pointer transition-colors",
          active ? "bg-elevated" : "hover:bg-elevated/60",
        )}
      >
        <div className="mt-0.5 shrink-0">
          {isLive
            ? <Radio className="h-3.5 w-3.5 text-accent animate-pulse" />
            : <ScrollText className="h-3.5 w-3.5 text-fgMuted" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-medium text-fg truncate">
            {title || sessionLabel(meta)}
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-fgMuted mt-0.5 font-mono">
            <span>{meta.final_count} caps</span>
            {meta.source_lang && (
              <span>· {shortLang(meta.source_lang)}→{shortLang(meta.target_lang)}</span>
            )}
            {meta.elapsed_s > 0 && <span>· {durationStr(meta.elapsed_s)}</span>}
            {!isLive && meta.started_at && <span>· {timeAgo(meta.started_at)}</span>}
          </div>
        </div>
        {onDelete && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-fgMuted hover:text-danger p-1 -m-1"
            title="Delete session JSONL + SRT"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </li>
  );
}

function sessionLabel(s: SessionMeta) {
  if (!s.started_at) return s.id;
  const d = new Date(s.started_at);
  return d.toLocaleString("en-GB", {
    weekday: "short", day: "2-digit", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
}

function shortLang(code: string) {
  if (!code) return "?";
  return code.split("-")[0];
}

// A pretend SessionMeta for the in-memory "Live" entry. final_count is
// the running transcript length so the sidebar reflects the live
// caption count without a server fetch.
function liveSessionPseudoMeta(captionsSoFar: number): SessionMeta {
  return {
    id: "__live__",
    started_at: "",
    ended_at: "",
    source_lang: "",
    target_lang: "",
    audio_source: "",
    device: null,
    file: null,
    final_count: captionsSoFar,
    elapsed_s: 0,
    jsonl_url: "",
    srt_url: null,
    active: true,
    size_bytes: 0,
  };
}
