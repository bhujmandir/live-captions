import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useStore } from "@/store";
import { cn } from "@/lib/utils";
import { deskNotice } from "@/link-health";
import { useSocketState } from "@/use-socket-state";

// What the person running the desk sees about the health of the pipeline.
//
// The question they are actually asking, mid-katha, with a full hall, is
// "is this thing working?" — and until now a blank caption bar answered it
// two contradictory ways: nobody is speaking, or the connection has died.
// These two pieces separate them:
//
//   LinkChip   — always up while capturing, small and calm. Names which of
//                the four states we are in.
//   LinkBanner — only when the link is down, and then unmissable. A fault
//                should not have to be looked for.
//
// Four states, not two, because a deliberate close (a direction flip, or the
// service hanging up an idle connection) recovers on its own in about a
// second. Painting that in fault red would teach the operator to ignore the
// one colour that matters.

type Status = "down" | "reconnecting" | "silent" | "listening";

// How long the audio has to stay under the gate threshold before we call it
// silence. Shorter than this and the chip flickers on the gaps between words.
const SILENCE_HOLD_MS = 2000;

function useStatus(): Status {
  const link    = useStore((s) => s.link);
  const socket  = useSocketState();
  const peak    = useStore((s) => s.audioPeak);
  const running = useStore((s) => s.running);
  // The same threshold the server's gate uses — it is posted from here on
  // Start, so the chip agrees with what is actually being sent.
  const threshold = useStore((s) => s.settings.silencePct) / 100;

  // `deskNotice` is the whole decision, and it lives outside this file
  // because it was wrong here and could not be tested here. Issue #57: the
  // banner below was gated behind `running`, and `running` is precisely
  // what a page with no socket cannot know — so a desk that had not pressed
  // Start, or that reloaded during the outage, was shown nothing at all
  // while the server was gone.
  const notice = deskNotice(socket, { state: link.state, reason: link.reason, running });
  const down = notice !== "none";
  const [quiet, setQuiet] = useState(false);

  useEffect(() => {
    if (!running || down || (peak ?? 0) >= threshold) {
      setQuiet(false);
      return;
    }
    const t = setTimeout(() => setQuiet(true), SILENCE_HOLD_MS);
    return () => clearTimeout(t);
  }, [peak, running, down, threshold]);

  if (notice !== "none") return notice;
  return quiet ? "silent" : "listening";
}

const CHIP: Record<Status, { label: string; title: string; className: string; dot: string }> = {
  down: {
    label: "No connection",
    title: "The speech service cannot be reached — captions have stopped and the overlay is blank",
    className: "border-danger/50 bg-danger/15 text-danger",
    dot: "bg-danger rec-pulse",
  },
  reconnecting: {
    label: "Reconnecting",
    title: "The connection closed and is being reopened — normal after a language flip or a long pause",
    className: "border-warn/40 bg-warn/10 text-warn",
    dot: "bg-warn rec-pulse",
  },
  silent: {
    label: "Silent",
    title: "Connected and listening — nobody is speaking",
    className: "border-border bg-elevated/60 text-fgMuted",
    dot: "bg-muted",
  },
  listening: {
    label: "Listening",
    title: "Connected, and audio is coming through",
    className: "border-success/40 bg-success/10 text-success",
    dot: "bg-success",
  },
};

export function LinkChip() {
  const status = useStatus();
  const chip   = CHIP[status];

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1.5",
        "text-[10px] font-mono uppercase tracking-[0.16em] transition-colors",
        chip.className,
      )}
      title={chip.title}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", chip.dot)} />
      {chip.label}
    </span>
  );
}

export function LinkBanner() {
  const status  = useStatus();
  const link    = useStore((s) => s.link);
  const conn    = useStore((s) => s.conn);

  if (status !== "down" && status !== "reconnecting") return null;

  const serverGone = conn !== "open";
  const fault      = status === "down";

  return (
    <div
      role="status"
      className={cn(
        "absolute inset-x-0 top-0 z-20 flex items-center gap-3 px-5 py-3",
        "border-b backdrop-blur-sm",
        fault ? "border-danger/50 bg-danger/20" : "border-warn/40 bg-warn/15",
      )}
    >
      <AlertTriangle className={cn("h-5 w-5 shrink-0", fault ? "text-danger" : "text-warn")} />
      <div className="min-w-0">
        <div className={cn(
          "text-sm font-semibold uppercase tracking-[0.14em]",
          fault ? "text-danger" : "text-warn",
        )}>
          {fault ? "No connection — captions have stopped" : "Reconnecting"}
        </div>
        <div className="text-xs text-fgMuted">
          {serverGone
            ? "This page cannot reach the captions server. It has stopped, or it was "
              + "never started — check the server window, or run start-captions.bat."
            : fault
              ? `The screen has been cleared so the hall is not left reading an old line. Retrying — attempt ${link.attempt + 1}.`
              : "The connection closed and is being reopened. Normal after a language flip or a long pause."}
        </div>
      </div>
    </div>
  );
}
