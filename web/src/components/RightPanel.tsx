import { useRef } from "react";
import { activeRun, useStore, type RightTab } from "../state/store";
import { ChatPanel } from "./ChatPanel";
import { ReelPanel } from "./ReelPanel";
import { TracePanel } from "./TracePanel";

const TABS: { id: RightTab; label: string }[] = [
  { id: "chat", label: "Chat" },
  { id: "trace", label: "Trace" },
  { id: "reel", label: "Reel" },
];

export function RightPanel() {
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  const live = useStore(activeRun);
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const i = TABS.findIndex((t) => t.id === tab);
    const next = TABS[(i + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length]!;
    setTab(next.id);
    refs.current[next.id]?.focus();
  };

  return (
    <aside className="panel">
      <div className="tabs" role="tablist" aria-label="Agent" onKeyDown={onKey}>
        {TABS.map((t) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className="tab"
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === "trace" && live && <span className="live-dot" aria-label="(running)" />}
          </button>
        ))}
      </div>
      <div className="panel-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "chat" && <ChatPanel />}
        {tab === "trace" && <TracePanel />}
        {tab === "reel" && <ReelPanel />}
      </div>
    </aside>
  );
}
