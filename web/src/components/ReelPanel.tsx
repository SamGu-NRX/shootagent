import { Clapperboard } from "lucide-react";
import { useEffect, useState } from "react";
import { sourceBlob, useStore } from "../state/store";

export function ReelPanel() {
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  const runs = useStore((s) => s.runs);
  const traces = useStore((s) => s.traces);
  const build = useStore((s) => s.buildReel);
  const busy = useStore((s) => s.busyAction === "reel");
  const setTab = useStore((s) => s.setTab);
  const setRun = useStore((s) => s.setTraceRun);
  const [videoState, setVideoState] = useState<"loading" | "ready" | "missing">("loading");

  const reelRuns = runs.filter((r) => r.mode === "reel");
  const last = reelRuns[reelRuns.length - 1];
  const lastTrace = last ? traces[last.id] : undefined;
  const rendering = lastTrace?.status === "running";
  const approved = shoot?.views.filter((v) => v.approved) ?? [];
  // cache-bust after each finished reel run
  const src = shoot ? `${api.reelUrl(shoot.id)}?run=${last?.id ?? "none"}` : null;

  useEffect(() => setVideoState("loading"), [src]);

  return (
    <div className="reel">
      <div className="reel-frame" data-state={videoState}>
        {src && !rendering && (
          <video
            key={src}
            src={src}
            controls
            playsInline
            preload="metadata"
            onLoadedMetadata={() => setVideoState("ready")}
            onError={() => setVideoState("missing")}
          />
        )}
        {(videoState !== "ready" || rendering) && (
          <div className="reel-placeholder">
            <Clapperboard size={20} strokeWidth={1.5} aria-hidden />
            {rendering ? (
              <p>Rendering the reel…</p>
            ) : videoState === "loading" ? (
              <p className="muted">Looking for a rendered reel…</p>
            ) : (
              <p>No reel yet. Build one from the approved photos.</p>
            )}
          </div>
        )}
      </div>

      <div className="reel-side">
        <p className="reel-count num">
          {approved.length} of {shoot?.views.length ?? 0} photos approved
        </p>
        {approved.length > 0 ? (
          <ol className="reel-thumbs">
            {approved.map((v) => {
              const blob = sourceBlob(v, { kind: "head" });
              return (
                <li key={v.id} title={v.label}>
                  {blob && shoot && <img src={api.blobUrl(shoot.id, blob, 160)} alt={v.label} loading="lazy" />}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="muted">The reel uses approved photos only. Approve photos in the viewer, or use Approve all.</p>
        )}
        <button className="btn btn-primary" onClick={build} disabled={!approved.length || busy || rendering}>
          {rendering ? "Rendering…" : last ? "Rebuild reel" : "Build reel"}
        </button>
        {last && (
          <button
            className="link link-quiet"
            onClick={() => {
              setRun(last.id);
              setTab("trace");
            }}
          >
            See the reel run in the trace
          </button>
        )}
        {lastTrace?.messages.map((m, i) => (
          <p key={i} className="muted small">
            {m}
          </p>
        ))}
      </div>
    </div>
  );
}
