import { useStore } from "@/store";
import { cn } from "@/lib/utils";
import { Settings as SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

// Header is the operator's broadcast console strip. Read at a distance:
//   • Wordmark (appName) — left edge, calm.
//   • REC indicator — dominant when running, recessed when idle. This
//     is the single most important live-state cue in the UI, sized and
//     coloured so a stage manager catches it from across the room.
//   • Connection pill — quiet plumbing status; only visible to operators
//     who are looking for it. Hides itself when the WS is healthy and
//     idle (the REC indicator already implies "WS open" when running,
//     so dual badging is noise).
//   • Settings — icon, low contrast.

export function Header() {
  const conn    = useStore((s) => s.conn);
  const running = useStore((s) => s.running);
  const appName = useStore((s) => s.appName);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);

  // We only call out the connection when it's NOT healthy. A green dot
  // permanently lit next to "OPEN" is just chrome — and when something
  // breaks, the absence of normal-state noise makes the warning louder.
  const connBad = conn !== "open";

  return (
    <header className="relative flex items-center justify-between gap-4 border-b border-border bg-surface/80 backdrop-blur-sm px-5 py-2.5">
      {/* Thin accent strip across the bottom when running — a subliminal
          on-air bar visible even when the REC pill is offscreen. */}
      <div
        aria-hidden
        className={cn(
          "absolute inset-x-0 bottom-0 h-px transition-opacity duration-300",
          running ? "bg-rec opacity-80" : "opacity-0",
        )}
      />

      <div className="flex items-baseline gap-2.5">
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          {appName}
        </span>
        <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-fgMuted/60">
          captions
        </span>
      </div>

      <div className="flex items-center gap-2">
        {connBad && (
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-mono uppercase tracking-[0.14em]",
              conn === "connecting"
                ? "border-warn/40 bg-warn/10 text-warn"
                : "border-danger/40 bg-danger/10 text-danger",
            )}
            title={`WebSocket: ${conn}`}
          >
            <span className={cn(
              "h-1.5 w-1.5 rounded-full",
              conn === "connecting" ? "bg-warn rec-pulse" : "bg-danger",
            )} />
            {conn === "connecting" ? "reconnecting" : "disconnected"}
          </span>
        )}

        <span
          className={cn(
            "inline-flex items-center gap-2 rounded-md border px-3 h-7 text-[11px] font-mono uppercase tracking-[0.18em] transition-colors",
            running
              ? "border-rec/40 bg-rec/10 text-rec"
              : "border-border bg-elevated/60 text-fgMuted",
          )}
          title={running ? "On air — captions are being broadcast" : "Idle — no active capture"}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              running ? "bg-rec rec-pulse" : "bg-muted",
            )}
          />
          {running ? "On air" : "Standby"}
        </span>

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setSettingsOpen(true)}
          title="Settings (layout, Sarvam, audio gate, overlay URL)"
        >
          <SettingsIcon className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
