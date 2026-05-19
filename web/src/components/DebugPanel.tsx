import { useEffect, useRef } from "react";
import { useStore } from "@/store";
import { Button } from "@/components/ui/button";
import { Bug, X, Eraser } from "lucide-react";

// Operator-only floating debug log. Records arrive on the WS bus:
// `log_snapshot` on connect (replay of the server's ring buffer) then
// live `log` events. State lives in the store so opening / closing the
// panel is free — no fetching, no cursor.

const LVL_COLOR: Record<string, string> = {
  DEBUG: "text-fgMuted",
  INFO: "text-success",
  WARNING: "text-warn",
  ERROR: "text-danger",
  CRITICAL: "text-danger",
};

export function DebugPanel() {
  const open      = useStore((s) => s.debugOpen);
  const setOpen   = useStore((s) => s.setDebugOpen);
  const rows      = useStore((s) => s.debugLogs);
  const clearLogs = useStore((s) => s.clearLogs);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Stick scroll to bottom only when the operator was already near the bottom.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [rows, open]);

  return (
    <>
      <button
        onClick={() => setOpen(!open)}
        title="Server log"
        className="fixed left-3 bottom-3 z-40 grid place-items-center h-9 w-9 rounded-full bg-elevated border border-border text-fgMuted hover:text-fg"
      >
        <Bug className="h-4 w-4" />
      </button>
      {open && (
        <div className="fixed left-3 bottom-14 z-40 w-[min(720px,calc(100vw-1.5rem))] max-h-[40vh] bg-bg/95 backdrop-blur border border-border rounded-md shadow-2xl flex flex-col overflow-hidden">
          <header className="flex items-center justify-between px-3 py-1.5 border-b border-border text-xs">
            <strong className="text-fgMuted">Server log</strong>
            <div className="flex gap-1">
              <Button variant="ghost" size="sm" onClick={clearLogs} title="Clear (local only)">
                <Eraser className="h-3 w-3" />
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)} title="Hide">
                <X className="h-3 w-3" />
              </Button>
            </div>
          </header>
          <div ref={listRef} className="flex-1 overflow-y-auto p-2 font-mono text-[11px] leading-snug">
            {rows.map((r, i) => (
              <div key={`${r.t}-${i}`} className={LVL_COLOR[r.level] || "text-fgMuted"}>
                {new Date(r.t * 1000).toLocaleTimeString("en-GB", { hour12: false })}  {r.level.padEnd(7)}  {r.msg}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
