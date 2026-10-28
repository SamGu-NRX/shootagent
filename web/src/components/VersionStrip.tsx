import { Check, Redo2, Undo2 } from "lucide-react";
import type { View } from "../api/types";
import { toolLabel } from "../lib/format";
import { useStore } from "../state/store";

/** The view's version history in creation order. Click previews a version on the right side;
 *  "Make current" moves the head (undo/redo also move the head). */
export function VersionStrip({ view }: { view: View }) {
  const b = useStore((s) => s.b);
  const setSource = useStore((s) => s.setSource);
  const moveHead = useStore((s) => s.moveHead);
  const undo = useStore((s) => s.undo);
  const redo = useStore((s) => s.redo);
  const busy = useStore((s) => s.busyAction === "head");
  const approve = useStore((s) => s.approve);

  const head = view.versions.find((v) => v.id === view.head);
  const canUndo = !!head?.parent;
  const canRedo = view.versions.some((v) => v.parent === view.head);
  const previewId = b.kind === "version" ? b.id : view.head;
  const previewIsHead = previewId === view.head;

  return (
    <div className="versions" aria-label="Version history">
      <div className="versions-actions">
        <button className="icon-btn" onClick={() => undo()} disabled={!canUndo || busy} aria-label="Undo" title="Undo (Ctrl+Z)">
          <Undo2 size={15} />
        </button>
        <button className="icon-btn" onClick={() => redo()} disabled={!canRedo || busy} aria-label="Redo" title="Redo (Ctrl+Shift+Z)">
          <Redo2 size={15} />
        </button>
      </div>
      <ol className="versions-list">
        {view.versions.map((v) => {
          const isHead = v.id === view.head;
          return (
            <li key={v.id}>
              <button
                className="ver"
                aria-current={isHead ? "true" : undefined}
                aria-pressed={v.id === previewId}
                data-check={v.check ? (v.check.passed ? "pass" : "fail") : undefined}
                onClick={() => setSource("b", isHead ? { kind: "head" } : { kind: "version", id: v.id })}
                title={v.note || undefined}
              >
                <span className="ver-op">{v.op === "ingest" ? "AutoHDR original" : toolLabel(v.op)}</span>
                <span className="ver-id num">
                  {v.id}
                  {v.check && <span className="ver-check">{v.check.passed ? "✓" : "✗"}</span>}
                  {isHead && <span className="ver-head">current</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {!previewIsHead && previewId && (
        <button className="btn btn-sm" onClick={() => moveHead(view.id, previewId)} disabled={busy}>
          Make {previewId} current
        </button>
      )}
      <button
        className={view.approved ? "btn btn-approved" : "btn"}
        aria-pressed={view.approved}
        onClick={() => approve(view.id, !view.approved)}
        title={view.approved ? "Approved for delivery and the reel. Click to unapprove." : "Approve this photo for delivery and the reel"}
      >
        <Check size={14} aria-hidden /> {view.approved ? "Approved" : "Approve photo"}
      </button>
    </div>
  );
}
