import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useStore } from "@/store";
import { api } from "@/api";
import type { VodJob } from "@/types";
import { timeAgo, durationStr, cn } from "@/lib/utils";
import { RangeEditor } from "@/components/RangeEditor";
import {
  Film, Plus, Check, X, AlertCircle, Loader2, Download, ExternalLink,
  Clock, Trash2, Ban, Pause,
} from "lucide-react";

// Ordered stages shown in the progress visualisation. Mirrors
// tools.vod_pipeline.ORDERED_STAGES — keep in sync.
const STAGES: Array<{ key: string; label: string }> = [
  { key: "download_video",  label: "Download video"   },
  { key: "awaiting_ranges", label: "Pick ranges"      },
  { key: "extract_audio",   label: "Extract audio"    },
  { key: "upload_gcs",      label: "Upload to cloud"  },
  { key: "stt_batch",       label: "Transcribe"       },
  { key: "cue_group",       label: "Group cues"       },
  { key: "translate",       label: "Translate"        },
  { key: "apply_rules",     label: "Apply rules"      },
  { key: "write_outputs",   label: "Write SRT + preview" },
];

export function ReprocessTab() {
  const jobs    = useStore((s) => s.vodJobs);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Auto-select the first job (most recent) once we have data, and
  // re-select the newest job when a new one is enqueued so the operator
  // sees their submission immediately.
  useEffect(() => {
    if (!selectedId && jobs.length) setSelectedId(jobs[0].id);
  }, [jobs, selectedId]);

  const selected = jobs.find((j) => j.id === selectedId);

  return (
    <div className="h-full grid grid-rows-[auto_1fr] min-h-0">
      <NewJobBar onSubmitted={(j) => setSelectedId(j.id)} />

      {/* Job list + detail. No jobs = full-width empty state. */}
      {jobs.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-[20rem_1fr] min-h-0">
          <JobList selectedId={selectedId} onSelect={setSelectedId} />
          {selected ? <JobDetail job={selected} /> : <div className="p-8 text-fgMuted">Select a job.</div>}
        </div>
      )}
    </div>
  );
}

// ── New job bar ─────────────────────────────────────────────────────

function NewJobBar({ onSubmitted }: { onSubmitted: (j: VodJob) => void }) {
  const [url, setUrl]   = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string>("");

  const submit = async () => {
    if (!url.trim()) return;
    setErr(""); setBusy(true);
    try {
      const r = await api.createVodJob(url.trim());
      setUrl("");
      onSubmitted(r.job);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-border bg-surface p-4">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3">
          <Film className="h-5 w-5 text-accent shrink-0" />
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="Paste a YouTube URL or video ID — e.g. https://youtu.be/dQw4w9WgXcQ"
            disabled={busy}
            className="h-10 text-base"
          />
          <Button onClick={submit} disabled={busy || !url.trim()} size="lg">
            {busy
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Queuing…</>
              : <><Plus className="h-4 w-4" /> Reprocess</>
            }
          </Button>
        </div>
        {err && (
          <div className="mt-2 flex items-center gap-2 text-sm text-danger">
            <AlertCircle className="h-4 w-4" /> {err}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Empty state ─────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex items-center justify-center p-8">
      <div className="max-w-md text-center">
        <Film className="h-16 w-16 text-fgMuted/30 mx-auto mb-4" />
        <h3 className="text-lg font-semibold mb-2">No reprocess jobs yet</h3>
        <p className="text-sm text-fgMuted leading-relaxed">
          Paste a past YouTube broadcast URL above and click <strong className="text-fg">Reprocess</strong>.
          The tool will download the audio, re-transcribe with Google's chirp_2 model,
          apply your substitution rules, and produce an SRT you can upload to YouTube Studio.
          <br /><br />
          <span className="text-fgMuted">3-hour VODs take ~45–60 min end-to-end.</span>
        </p>
      </div>
    </div>
  );
}

// ── Job list ────────────────────────────────────────────────────────

function JobList({ selectedId, onSelect }: { selectedId: string | null; onSelect: (id: string) => void }) {
  const jobs = useStore((s) => s.vodJobs);
  return (
    <aside className="border-r border-border overflow-y-auto bg-surface/40">
      <ul className="divide-y divide-border">
        {jobs.map((j) => (
          <li key={j.id}>
            <button
              onClick={() => onSelect(j.id)}
              className={cn(
                "w-full text-left p-3 transition-colors flex items-center gap-3",
                selectedId === j.id ? "bg-elevated" : "hover:bg-elevated/60",
              )}
            >
              <StatusIcon status={j.status} />
              <div className="flex-1 min-w-0">
                <div className="font-mono text-xs text-fgMuted truncate">{j.video_id}</div>
                <div className="text-xs text-fgMuted mt-0.5">
                  <StatusLabel job={j} />
                </div>
              </div>
              <div className="text-[10px] text-fgMuted shrink-0">{timeAgo(j.created_at)}</div>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function StatusIcon({ status }: { status: VodJob["status"] }) {
  const cls = "h-4 w-4 shrink-0";
  if (status === "running")          return <Loader2 className={cn(cls, "text-accent animate-spin")} />;
  if (status === "awaiting_ranges")  return <Pause   className={cn(cls, "text-warn")} />;
  if (status === "done")             return <Check   className={cn(cls, "text-success")} />;
  if (status === "failed")           return <X       className={cn(cls, "text-danger")} />;
  if (status === "cancelled")        return <Ban     className={cn(cls, "text-fgMuted")} />;
  return <Clock className={cn(cls, "text-fgMuted")} />;
}

function StatusLabel({ job }: { job: VodJob }) {
  if (job.status === "done")            return <>Done · {job.cue_count} cues · {job.rules_fired_count} edits</>;
  if (job.status === "failed")          return <span className="text-danger">Failed</span>;
  if (job.status === "queued")          return <>Queued</>;
  if (job.status === "running")         return <>{job.stage_label || "Running…"}</>;
  if (job.status === "awaiting_ranges") return <span className="text-warn">Awaiting range selection</span>;
  if (job.status === "cancelled")       return <>Cancelled</>;
  return <>{job.status}</>;
}

// ── Job detail ──────────────────────────────────────────────────────

function JobDetail({ job }: { job: VodJob }) {
  const [busy, setBusy] = useState(false);

  const cancel = async () => {
    if (!confirm("Cancel this job?")) return;
    setBusy(true);
    try { await api.cancelVodJob(job.id); } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!confirm("Remove this job from history? (Files on disk are kept.)")) return;
    setBusy(true);
    try { await api.deleteVodJob(job.id); } finally { setBusy(false); }
  };

  return (
    <section className="overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-6">
        {/* Header */}
        <header className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold font-mono">{job.video_id}</h2>
            <a
              href={job.video_url} target="_blank" rel="noreferrer"
              className="text-xs text-accent hover:underline inline-flex items-center gap-1 mt-0.5"
            >
              {job.video_url} <ExternalLink className="h-3 w-3" />
            </a>
            <div className="mt-2 flex items-center gap-2 text-xs">
              <Badge variant={
                job.status === "done"      ? "success" :
                job.status === "failed"    ? "danger"  :
                job.status === "running"   ? "accent"  :
                job.status === "cancelled" ? "outline" : "default"
              }>
                {job.status}
              </Badge>
              {job.created_at && <span className="text-fgMuted">created {timeAgo(job.created_at)}</span>}
            </div>
          </div>
          <div className="flex gap-2">
            {(job.status === "queued" || job.status === "running") && (
              <Button variant="outline" size="sm" onClick={cancel} disabled={busy}>
                <Ban className="h-4 w-4" /> Cancel
              </Button>
            )}
            {(job.status === "done" || job.status === "failed" || job.status === "cancelled") && (
              <Button variant="ghost" size="sm" onClick={remove} disabled={busy}>
                <Trash2 className="h-4 w-4" /> Remove
              </Button>
            )}
          </div>
        </header>

        {/* Error */}
        {job.status === "failed" && (
          <div className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <pre className="whitespace-pre-wrap font-sans">{job.error}</pre>
          </div>
        )}

        {/* Awaiting ranges — interactive picker takes over the detail
            pane until the operator submits. The progress stepper is
            hidden in this mode; once they hit Transcribe the job flips
            back to "running" and StagesView resumes. */}
        {job.status === "awaiting_ranges" && job.mp4_url && (
          <AwaitingRangesView job={job} />
        )}

        {/* Progress */}
        {job.status !== "done" && job.status !== "cancelled" && job.status !== "awaiting_ranges" && (
          <StagesView job={job} />
        )}

        {/* Done — preview + download */}
        {job.status === "done" && <DoneView job={job} />}
      </div>
    </section>
  );
}

function AwaitingRangesView({ job }: { job: VodJob }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState<string>("");

  const submit = async (ranges: { start_s: number; end_s: number }[]) => {
    setErr(""); setBusy(true);
    try {
      await api.transcribeVodJob(job.id, ranges);
      // The job's status flips via WS broadcast — no need to update locally.
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-warn/40 bg-warn/5 p-3 text-sm text-warn flex items-start gap-2">
        <Pause className="h-4 w-4 shrink-0 mt-0.5" />
        <div>
          <strong>Pick the sections to transcribe.</strong> Saves GCP STT cost
          and keeps the SRT focused on the speech. Submit with no ranges to
          transcribe the whole video.
        </div>
      </div>
      {err && (
        <div className="rounded-md border border-danger/40 bg-danger/10 p-2.5 text-sm text-danger flex items-start gap-2">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" /><span>{err}</span>
        </div>
      )}
      <RangeEditor
        mp4Url={job.mp4_url}
        initial={(job.ranges || []) as any}
        busy={busy}
        onSubmit={submit}
      />
    </div>
  );
}

function StagesView({ job }: { job: VodJob }) {
  // Map server stage to position in our ordered list. Stages before the
  // current one are done; the current one shows its pct; later ones are pending.
  const currentIdx = STAGES.findIndex((s) => s.key === job.stage);
  return (
    <div className="rounded-md border border-border bg-surface p-4">
      <ol className="space-y-3">
        {STAGES.map((s, idx) => {
          const done    = currentIdx > idx || job.status === "done";
          const current = currentIdx === idx && job.status === "running";
          const pending = currentIdx < idx && !done;
          return (
            <li key={s.key} className="flex items-start gap-3 text-sm">
              <span className={cn(
                "h-5 w-5 rounded-full shrink-0 grid place-items-center mt-0.5",
                done    && "bg-success/20 text-success",
                current && "bg-accent/20 text-accent",
                pending && "bg-elevated text-fgMuted",
              )}>
                {done    && <Check className="h-3 w-3" />}
                {current && <Loader2 className="h-3 w-3 animate-spin" />}
                {pending && <span className="text-[10px] font-mono">{idx + 1}</span>}
              </span>
              <div className="flex-1 min-w-0">
                <div className={cn(
                  "flex items-center justify-between gap-2",
                  pending && "text-fgMuted",
                )}>
                  <span>{s.label}</span>
                  {current && job.stage_pct != null && (
                    <span className="font-mono text-xs text-fgMuted">
                      {Math.round(job.stage_pct * 100)}%
                    </span>
                  )}
                  {(done || current) && job.stage_elapsed_s > 0 && current && (
                    <span className="font-mono text-xs text-fgMuted">
                      {durationStr(job.stage_elapsed_s)}
                    </span>
                  )}
                </div>
                {current && job.stage_pct != null && (
                  <Progress value={(job.stage_pct || 0) * 100} className="mt-1.5" />
                )}
                {current && job.stage_detail && (
                  <div className="text-xs text-fgMuted mt-1">{job.stage_detail}</div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function DoneView({ job }: { job: VodJob }) {
  // Video preview uses /results/... static-serving mount in
  // live_captions.py. The <track> element loads the SRT directly so
  // the operator can scrub through and verify timing in-browser before
  // uploading to YouTube Studio.
  const srtName = job.srt_url.split("/").pop() || "subtitles.srt";

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Cues"          value={job.cue_count.toString()} />
        <Stat label="Rules fired"   value={`${job.rules_fired_count} of ${job.cue_count}`} />
        <Stat label="Duration"      value={
          job.started_at && job.finished_at
            ? durationStr((new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000)
            : "—"
        } />
      </div>

      {/* Inline preview — video + auto-loaded SRT track */}
      {job.mp4_url && (
        <div className="rounded-md border border-border bg-black overflow-hidden">
          <video controls crossOrigin="anonymous" className="w-full max-h-[60vh]">
            <source src={job.mp4_url} type="video/mp4" />
            <track default kind="subtitles" srcLang="en"
              label="English (reprocessed)" src={job.srt_url} />
          </video>
        </div>
      )}

      {/* Download + Studio reminder */}
      <div className="rounded-md border border-accent/30 bg-accent/5 p-4">
        <h3 className="text-sm font-semibold flex items-center gap-2 mb-2">
          <Download className="h-4 w-4 text-accent" />
          Ready to upload
        </h3>
        <p className="text-sm text-fgMuted leading-relaxed mb-3">
          Open YouTube Studio → this video → Subtitles → Add language (or replace the existing
          English track) → Upload file → <strong className="text-fg">With timing</strong>.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <a href={job.srt_url} download={srtName}>
              <Download className="h-4 w-4" /> Download SRT
            </a>
          </Button>
          {job.preview_url && (
            <Button variant="outline" asChild>
              <a href={job.preview_url} target="_blank" rel="noreferrer">
                <ExternalLink className="h-4 w-4" /> Open full preview page
              </a>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <div className="text-[10px] uppercase tracking-wider text-fgMuted font-mono">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}
