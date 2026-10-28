import { Check } from "lucide-react";
import { useEffect, useRef } from "react";
import { statusText, viewStatus } from "../lib/format";
import { sourceBlob, useStore } from "../state/store";

export function Filmstrip() {
  const shoot = useStore((s) => s.shoot);
  const viewId = useStore((s) => s.viewId);
  const runs = useStore((s) => s.runs);
  const api = useStore((s) => s.api);
  const select = useStore((s) => s.selectView);
  const listRef = useRef<HTMLOListElement>(null);
  const qcRan = runs.some((r) => r.mode === "qc" && r.done);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>("[aria-current=true]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [viewId]);

  return (
    <nav className="strip" aria-label="Photos in this shoot">
      <ol ref={listRef} className="strip-list">
        {!shoot &&
          Array.from({ length: 6 }, (_, i) => (
            <li key={i}>
              <div className="strip-item skeleton-thumb" />
            </li>
          ))}
        {shoot?.views.map((v, i) => {
          const status = viewStatus(v, qcRan);
          const blob = sourceBlob(v, { kind: "head" });
          return (
            <li key={v.id}>
              <button className="strip-item" aria-current={v.id === viewId} onClick={() => select(v.id)}>
                <span className="strip-thumb">
                  {blob && <img src={api.blobUrl(shoot.id, blob, 320)} alt="" loading="lazy" decoding="async" />}
                  {v.approved && (
                    <span className="strip-approved" aria-hidden>
                      <Check size={11} strokeWidth={3} />
                    </span>
                  )}
                </span>
                <span className="strip-meta">
                  <span className="strip-label">
                    <span className="strip-n num" aria-hidden>
                      {i + 1}
                    </span>
                    {v.label || v.room || v.id}
                  </span>
                  <span className="strip-status" data-status={status}>
                    <span className="status-dot" aria-hidden />
                    {statusText(status, v)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
