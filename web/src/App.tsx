import { useEffect } from "react";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { SettingsDrawer } from "@/components/SettingsDrawer";
import { DebugPanel }    from "@/components/DebugPanel";
import { LiveTab } from "@/tabs/LiveTab";
import { OutputsTab } from "@/tabs/OutputsTab";
import { RulesTab } from "@/tabs/RulesTab";
import { ReprocessTab } from "@/tabs/ReprocessTab";
import { TranscriptTab } from "@/tabs/TranscriptTab";
import { OverlayView } from "@/OverlayView";
import { useStore } from "@/store";
import { connectWs } from "@/api";
import { isOverlay } from "@/settings";

export default function App() {
  // Overlay surface is a completely separate render tree — no chrome,
  // no sidebar, no header. Just the caption block.
  if (isOverlay) return <OverlayView />;
  return <OperatorApp />;
}

function OperatorApp() {
  const tab = useStore((s) => s.tab);

  useEffect(() => { connectWs(); }, []);

  return (
    <div className="h-full grid grid-rows-[auto_1fr] bg-bg text-fg">
      <Header />
      <div className="grid grid-cols-[14rem_1fr] min-h-0">
        <Sidebar />
        <main className="overflow-hidden">
          {tab === "live" && <LiveTab />}
          {tab === "outputs" && <OutputsTab />}
          {tab === "rules" && <RulesTab />}
          {tab === "reprocess" && <ReprocessTab />}
          {tab === "transcript" && <TranscriptTab />}
        </main>
      </div>
      <SettingsDrawer />
      <DebugPanel />
    </div>
  );
}
