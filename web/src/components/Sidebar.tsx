import { useStore } from "@/store";
import { cn } from "@/lib/utils";
import {
  Radio, Youtube, Wand2, Film, ScrollText,
} from "lucide-react";

type Item = {
  id:    "live" | "outputs" | "rules" | "reprocess" | "transcript";
  label: string;
  icon:  React.ElementType;
  hint?: string;
};

// Order chosen to match operator flow: start the broadcast (Live), point
// it at outputs (Outputs), tune what the audience sees (Rules), reprocess
// past sessions (Reprocess), review history (Transcript). VOD reprocess
// is the new headline feature — given a colour accent in the sidebar.
const items: Item[] = [
  { id: "live",       label: "Live",       icon: Radio,      hint: "Broadcast captions" },
  { id: "outputs",    label: "Outputs",    icon: Youtube,    hint: "YouTube CC tracks"  },
  { id: "rules",      label: "Rules",      icon: Wand2,      hint: "Word substitutions" },
  { id: "reprocess",  label: "Reprocess",  icon: Film,       hint: "Fix past VODs"      },
  { id: "transcript", label: "Transcript", icon: ScrollText, hint: "Session log"        },
];

export function Sidebar() {
  const tab    = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);

  // Small counts shown on certain tabs — kept here so the sidebar can
  // surface "you have 3 jobs queued" without us repeating that pattern
  // in every tab body.
  const queued = useStore((s) => s.vodJobs.filter((j) => j.status === "queued" || j.status === "running").length);
  const rules  = useStore((s) => s.rules.length);
  const feeds  = useStore((s) => s.feeds.filter((f) => f.enabled).length);

  const badge = (id: Item["id"]) => {
    if (id === "reprocess" && queued > 0) return queued.toString();
    if (id === "rules"     && rules  > 0) return rules.toString();
    if (id === "outputs"   && feeds  > 0) return `${feeds} on`;
    return null;
  };

  return (
    <nav className="border-r border-border bg-surface/60 overflow-y-auto">
      <ul className="flex flex-col gap-px p-2">
        {items.map((it) => {
          const Icon = it.icon;
          const active = tab === it.id;
          const b = badge(it.id);
          return (
            <li key={it.id}>
              <button
                onClick={() => setTab(it.id)}
                className={cn(
                  // 2px accent strip on the leading edge for the active
                  // tab — a Linear-style anchor that's quieter than a
                  // full-fill background but still unambiguous.
                  "relative w-full flex items-center gap-3 rounded-md pl-4 pr-3 py-2 text-[13px] transition-colors",
                  "before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-[2px] before:rounded-r-sm",
                  active
                    ? "bg-elevated/80 text-fg before:bg-accent"
                    : "text-fgMuted hover:bg-elevated/40 hover:text-fg before:bg-transparent",
                )}
                title={it.hint}
              >
                <Icon className={cn("h-4 w-4 transition-colors", active ? "text-accent" : "text-fgMuted/80")} />
                <span className="flex-1 text-left tracking-tight">{it.label}</span>
                {b && (
                  <span className={cn(
                    "text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded",
                    active
                      ? "bg-accent/15 text-accent"
                      : "bg-border/60 text-fgMuted",
                  )}>
                    {b}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
