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
import type { Feed } from "@/types";
import { Plus, Trash2, Pencil, Youtube } from "lucide-react";
import { cn } from "@/lib/utils";

export function OutputsTab() {
  const feeds = useStore((s) => s.feeds);
  const [editing, setEditing] = useState<Partial<Feed> | null>(null);
  const liveCount = feeds.filter((f) => f.enabled).length;

  const toggle = async (f: Feed) => {
    if (!f.configured) return;
    try { f.enabled ? await api.disableFeed(f.id) : await api.enableFeed(f.id); }
    catch (e) { console.warn("toggle failed", e); }
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <Youtube className="h-5 w-5 text-accent" /> YouTube CC outputs
            </h2>
            <p className="text-sm text-fgMuted mt-1">
              Each enabled feed pushes captions to a separate YouTube live stream.
              The toggle is the kill switch.
            </p>
          </div>
          <Button onClick={() => setEditing({})}>
            <Plus className="h-4 w-4" /> Add feed
          </Button>
        </div>

        <div className="flex items-center gap-3 mb-4 text-sm">
          <Badge variant={liveCount > 0 ? "success" : "default"}>
            {liveCount}/{feeds.length} live
          </Badge>
          {feeds.length > 0 && (
            liveCount > 0 ? (
              <Button variant="danger" size="sm" onClick={() => api.disableAllFeeds()}>
                Disable all
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={() => api.enableAllFeeds()}>
                Enable all
              </Button>
            )
          )}
        </div>

        <div className="space-y-2">
          {feeds.length === 0 && (
            <div className="border border-border border-dashed rounded-lg p-8 text-center text-fgMuted">
              No feeds yet. Click <span className="text-fg font-medium">+ Add feed</span> to create one.
            </div>
          )}
          {feeds.map((f) => (
            <div
              key={f.id}
              className={cn(
                "flex items-center gap-3 rounded-lg border p-3",
                f.enabled
                  ? "border-success/40 bg-success/5"
                  : f.configured
                    ? "border-border bg-surface"
                    : "border-border bg-surface opacity-60",
              )}
            >
              <Switch
                checked={f.enabled}
                onCheckedChange={() => toggle(f)}
                disabled={!f.configured}
                title={f.configured ? (f.enabled ? "Pushing to YouTube — click to mute" : "Click to push") : "Add a stream key first"}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium truncate">{f.label || "(unnamed)"}</span>
                  <Badge variant="outline">{f.target_lang}</Badge>
                </div>
                <div className="font-mono text-[11px] text-fgMuted mt-0.5">
                  key …{f.stream_key_tail || "—"} · sent {f.sent || 0} · advance {(f.advance_sec || 0).toFixed(1)}s
                  {f.errors > 0 && <span className="text-danger"> · {f.errors} errors</span>}
                </div>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setEditing(f)} title="Edit">
                <Pencil className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={async () => {
                  if (!confirm(`Delete feed "${f.label}"? Stream key will be permanently removed.`)) return;
                  await api.deleteFeed(f.id);
                }}
                title="Delete"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* `key` forces a fresh dialog instance per edit-target, so useState
          inside the dialog re-initialises from props each time. Simpler
          and less buggy than mirroring props in an effect. */}
      {editing !== null && (
        <FeedEditDialog
          key={editing.id || "_new"}
          initial={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function FeedEditDialog({ initial, onClose }: { initial: Partial<Feed>; onClose: () => void }) {
  const sarvamLangs           = useStore((s) => s.sarvamLangs);
  const [label, setLabel]     = useState(initial.label || "");
  const [key, setKey]         = useState("");
  const [target, setTarget]   = useState(initial.target_lang || "en-IN");
  const [advance, setAdvance] = useState(String(initial.advance_sec ?? 1.5));

  const save = async () => {
    const body: Partial<Feed> & { stream_key?: string } = {
      label: label.trim() || "(unnamed)",
      target_lang: target,
      advance_sec: parseFloat(advance) || 1.5,
    };
    if (initial.id) body.id = initial.id;
    if (key.trim()) body.stream_key = key.trim();
    if (!initial.id && !body.stream_key) {
      alert("Stream key is required for a new feed.");
      return;
    }
    try { await api.saveFeed(body); onClose(); }
    catch (e: any) { alert("Save failed: " + e.message); }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{initial.id ? "Edit feed" : "New YouTube CC feed"}</DialogTitle>
          <DialogDescription>
            One feed = one YouTube live stream. The stream key lives in YouTube
            Studio → Live → Captions → POST captions to URL.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="lbl">Label</Label>
            <Input id="lbl" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Main channel, Hindi track, …" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="key">Stream key</Label>
            <Input
              id="key" type="text" value={key} onChange={(e) => setKey(e.target.value)}
              autoComplete="off" spellCheck={false}
              placeholder={initial.stream_key_tail
                ? `paste to replace (current …${initial.stream_key_tail})`
                : "paste a YouTube CC stream key"}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="tgt">Target language</Label>
              <select id="tgt" value={target} onChange={(e) => setTarget(e.target.value)}
                className="h-9 rounded-md border border-border bg-surface px-3 text-sm">
                {sarvamLangs.map(([code, name]) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="adv">Advance (seconds)</Label>
              <Input id="adv" type="number" step="0.5" min="0" max="30"
                value={advance} onChange={(e) => setAdvance(e.target.value)} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save}>{initial.id ? "Save" : "Add feed"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

