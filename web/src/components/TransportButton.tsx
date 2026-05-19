import { cn } from "@/lib/utils";

// The Start/Stop control for the live capture pipeline. Visually a
// tinted-ghost chip rather than a solid-fill marketing button — the
// surrounding bar is full of h-9 controls and the transport button
// reads as a confident member of that row, not a CTA bolted on.
//
// State communication:
//   • idle  → calm green dot + "Start" on a success-tinted surface
//   • on-air → pulsing red dot + "Stop" on a rec-tinted surface
//
// The colour is the state — no icon required, no label change beyond
// the verb. A stage manager reads it correctly at a glance.

export function TransportButton({
  running,
  disabled,
  onClick,
}: {
  running:  boolean;
  disabled: boolean;
  onClick:  () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={
        running
          ? "Stop captures and close the Sarvam session"
          : "Begin capturing audio and streaming to Sarvam"
      }
      className={cn(
        "inline-flex items-center gap-2.5 h-9 px-5 min-w-[120px]",
        "rounded-md text-[13px] font-semibold tracking-tight justify-center",
        "border transition-[background-color,border-color,color,box-shadow,transform] duration-100",
        "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.04)]",
        "active:translate-y-px",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        "disabled:opacity-40 disabled:pointer-events-none disabled:cursor-not-allowed",
        running
          ? "bg-rec/10 border-rec/40 text-rec hover:bg-rec/15 hover:border-rec/60 focus-visible:ring-rec/50"
          : "bg-success/[0.08] border-success/30 text-success hover:bg-success/15 hover:border-success/55 focus-visible:ring-success/40",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          running ? "bg-rec rec-pulse" : "bg-success",
        )}
      />
      {running ? "Stop" : "Start"}
    </button>
  );
}
