import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { api } from "./api";
import { useStore } from "./store";

// Overlay mode (PP Web Object) must render with a transparent body so
// chroma-key isn't required. Detected from the URL before React mounts
// so there's no flash of dark background.
if (new URLSearchParams(location.search).get("overlay") === "1") {
  document.body.classList.add("overlay");
}

const rootEl = document.getElementById("root")!;
const root   = ReactDOM.createRoot(rootEl);

// Boot config drives document.title, the --accent CSS variable, and the
// lang dropdowns. The React UI is hard-coupled to its contents (empty
// sarvamLangs ⇒ empty Source/Target selects ⇒ unusable Live tab), so a
// failure here is fatal for the surface. We render a blocking error
// page with the failure detail and a retry button instead of silently
// falling back — better the operator sees "can't reach server" than a
// half-rendered UI that mysteriously won't start.
async function boot() {
  try {
    const cfg = await api.getConfig();
    useStore.getState().applyConfig(cfg);
    root.render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    );
  } catch (e: any) {
    const detail = e?.message ? String(e.message) : String(e);
    root.render(<BootError detail={detail} onRetry={boot} />);
  }
}

function BootError({ detail, onRetry }: { detail: string; onRetry: () => void }) {
  const [retrying, setRetrying] = React.useState(false);
  const handleRetry = async () => {
    setRetrying(true);
    try   { await onRetry(); }
    finally { setRetrying(false); }
  };
  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-bg text-fg">
      <div className="relative max-w-md w-full bg-surface border border-border rounded-lg overflow-hidden">
        {/* Red status strip on the leading edge — the visual idiom for
            a system error on a broadcast console. */}
        <div aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-danger" />

        <div className="px-6 pt-6 pb-5 space-y-5">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-danger rec-pulse" />
            <span className="text-[10px] font-mono uppercase tracking-[0.22em] text-danger">
              boot · fault
            </span>
          </div>

          <div className="space-y-1.5">
            <h1 className="text-base font-semibold tracking-tight text-fg">
              Captions server unreachable
            </h1>
            <p className="text-[13px] text-fgMuted leading-relaxed">
              <code className="text-fg font-mono text-[12px]">GET /api/config</code> failed,
              so the UI can't render — the lang matrix and branding live
              behind that endpoint.
            </p>
          </div>

          <pre className="text-[11px] font-mono leading-relaxed bg-bg border border-border/60 rounded-md px-3 py-2.5 text-fgMuted overflow-x-auto whitespace-pre-wrap">
            {detail || "(no error detail)"}
          </pre>

          <div className="text-[12px] text-fgMuted leading-relaxed space-y-2">
            <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-fgMuted/70">
              Likely
            </div>
            <ul className="space-y-1.5 pl-3">
              <li className="relative before:absolute before:left-[-12px] before:top-[7px] before:h-1 before:w-1 before:rounded-full before:bg-border">
                The Python server isn't running — start it with{" "}
                <code className="font-mono text-fg">uv run python live_captions.py</code>
              </li>
              <li className="relative before:absolute before:left-[-12px] before:top-[7px] before:h-1 before:w-1 before:rounded-full before:bg-border">
                Wrong port — this page expects the backend on the same host:port
              </li>
              <li className="relative before:absolute before:left-[-12px] before:top-[7px] before:h-1 before:w-1 before:rounded-full before:bg-border">
                A reverse proxy is dropping <code className="font-mono text-fg">/api/*</code>
              </li>
            </ul>
          </div>
        </div>

        <div className="border-t border-border/60 bg-elevated/40 px-6 py-3 flex justify-end">
          <button
            onClick={handleRetry}
            disabled={retrying}
            className={[
              "inline-flex items-center justify-center gap-2 h-8 px-4",
              "rounded-md text-[13px] font-semibold tracking-tight",
              "bg-accent text-accentFg",
              "shadow-[inset_0_1px_0_0_rgba(255,255,255,0.18)]",
              "transition-[filter,transform] duration-75 active:translate-y-px",
              "hover:brightness-110 disabled:opacity-50 disabled:pointer-events-none",
            ].join(" ")}
          >
            {retrying ? "Retrying…" : "Retry"}
          </button>
        </div>
      </div>
    </div>
  );
}

boot();
