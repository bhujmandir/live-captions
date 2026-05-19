import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from "@/components/ui/dialog";
import { useStore } from "@/store";
import { api } from "@/api";
import type { Rule } from "@/types";
import { Plus, Trash2, Pencil, Wand2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export function RulesTab() {
  const rules = useStore((s) => s.rules);
  const [editing, setEditing] = useState<Partial<Rule> | null>(null);
  const enabledCount = rules.filter((r) => r.enabled).length;

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <Wand2 className="h-5 w-5 text-accent" /> Substitution rules
            </h2>
            <p className="text-sm text-fgMuted mt-1">
              Applied to translated output before LED wall, ProPresenter, YouTube CC,
              and Pi displays. Edits take effect on the very next caption.
            </p>
          </div>
          <Button onClick={() => setEditing({})}>
            <Plus className="h-4 w-4" /> Add rule
          </Button>
        </div>

        <Badge variant={enabledCount > 0 ? "success" : "default"} className="mb-4">
          {enabledCount}/{rules.length} enabled
        </Badge>

        <div className="space-y-1.5">
          {rules.length === 0 && (
            <div className="border border-border border-dashed rounded-lg p-8 text-center text-fgMuted">
              No rules yet. Add one to control how religious vocabulary appears in captions.
            </div>
          )}
          {rules.map((r) => (
            <div
              key={r.id}
              className={cn(
                "flex items-center gap-3 rounded-lg border p-2.5 pl-3",
                r.error ? "border-danger/50 bg-danger/5"
                  : r.is_exclusion
                    ? r.enabled ? "border-warn/40 bg-warn/5" : "border-border bg-surface opacity-60"
                    : r.enabled ? "border-success/30 bg-success/5" : "border-border bg-surface opacity-60",
              )}
            >
              <Switch
                checked={r.enabled}
                onCheckedChange={(v) => api.saveRule({ id: r.id, enabled: v })}
              />
              <div className="flex-1 min-w-0 flex items-baseline gap-2 font-mono text-sm">
                <span className="text-fg">{r.pattern}</span>
                <span className="text-fgMuted">→</span>
                <span className={r.is_exclusion ? "text-warn" : "text-accent"}>{r.replacement}</span>
              </div>
              <div className="flex items-center gap-1">
                {r.is_exclusion && <Badge variant="warn">exclusion</Badge>}
                {r.regex         && <Badge variant="accent">regex</Badge>}
                {r.error         && (
                  <span title={r.error}>
                    <Badge variant="danger" className="flex items-center gap-1">
                      <AlertCircle className="h-3 w-3" /> error
                    </Badge>
                  </span>
                )}
              </div>
              <Button variant="ghost" size="icon" onClick={() => setEditing(r)}><Pencil className="h-4 w-4" /></Button>
              <Button
                variant="ghost" size="icon"
                onClick={async () => {
                  if (!confirm(`Delete rule for "${r.pattern}"?`)) return;
                  await api.deleteRule(r.id);
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {editing !== null && (
        <RuleEditDialog key={editing.id || "_new"} initial={editing} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}

function RuleEditDialog({ initial, onClose }: { initial: Partial<Rule>; onClose: () => void }) {
  const [pattern, setPattern]    = useState(initial.pattern || "");
  const [replacement, setReplacement] = useState(initial.replacement || "");
  const [regex, setRegex]        = useState(!!initial.regex);
  const [exclusion, setExclusion] = useState(!!initial.is_exclusion);

  const save = async () => {
    if (!pattern.trim()) { alert("Pattern is required."); return; }
    try {
      await api.saveRule({
        id: initial.id,
        pattern: pattern.trim(),
        replacement: exclusion ? "…" : replacement,
        regex,
        enabled: initial.enabled !== false,
      });
      onClose();
    } catch (e: any) { alert("Save failed: " + e.message); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{initial.id ? "Edit rule" : "New substitution rule"}</DialogTitle>
          <DialogDescription>
            Whole-word match by default (so "stories" wouldn't touch "history").
            Multi-word phrases are supported; longer phrases beat shorter ones.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="pat">Match</Label>
            <Input id="pat" value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="stories" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="rep">Replace with</Label>
            <Input
              id="rep" value={exclusion ? "…" : replacement}
              onChange={(e) => setReplacement(e.target.value)}
              disabled={exclusion}
              placeholder="katha"
            />
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} />
              <span>Regex pattern</span>
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox" checked={exclusion}
                onChange={(e) => { setExclusion(e.target.checked); if (e.target.checked) setReplacement("…"); }}
              />
              <span>Exclusion (replace with …)</span>
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save}>{initial.id ? "Save" : "Add rule"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
