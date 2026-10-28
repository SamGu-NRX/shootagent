import { Check, Clapperboard, X } from "lucide-react";
import { useEffect, useState } from "react";
import { duration, toolLabel } from "../lib/format";
import { sourceBlob, useStore } from "../state/store";
import type { ToolItem, TraceState } from "../trace/reduce";
import { LiveClock } from "./LiveClock";
import { MessageText } from "./MessageText";

/** URL of the video a reel run produced: render_reel's data.url, else the shoot's latest.mp4 once the run ended ok. */
function reelSrc(trace: TraceState | undefined, fallback: string): string | null {
  if (!trace) return null;
  const render = trace.items.find(
    (i): i is ToolItem => i.type === "tool" && i.tool === "render_reel" && i.status === "ok",
  );
  const url = typeof render?.result?.data?.url === "string" ? render.result.data.url : null;
  if (!url && trace.status !== "done") return null;
  // latest.mp4 is overwritten by every render, so the run id keeps the browser from replaying a cached older reel.
  return `${url ?? fallback}?run=${encodeURIComponent(trace.runId ?? "")}`;
}

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
  const trace = last ? traces[last.id] : undefined;
  const rendering = !!last && !last.done && trace?.status !== "done" && trace?.status !== "failed";
  const approved = shoot?.views.filter((v) => v.approved) ?? [];
  const src = shoot ? reelSrc(trace, api.reelUrl(shoot.id)) : null;
  const steps = (trace?.items.filter((i) => i.type === "tool") ?? []) as ToolItem[];

  useEffect(() => setVideoState("loading"), [src]);

  return (
    <div className="reel">
      <div className="reel-frame" data-state={videoState}>
        {src && (
          <video
            key={src}
            src={src}
            controls
            playsInline
            autoPlay
            muted
            loop
            preload="auto"
            onLoadedData={() => setVideoState("ready")}
            onError={() => setVideoState("missing")}
          />
        )}
        {(!src || videoState !== "ready") && (
          <div className="reel-placeholder">
            <Clapperboard size={20} strokeWidth={1.5} aria-hidden />
            {rendering ? (
              <p>
                Building the reel… <LiveClock since={trace?.startedAt ?? null} />
              </p>
            ) : src && videoState === "loading" ? (
              <p className="muted">Loading the reel…</p>
            ) : trace?.status === "failed" ? (
              <p>The reel run stopped before a video was made. See the trace for the reason, then build again.</p>
            ) : src && videoState === "missing" ? (
              <p>The reel run finished but the video didn't load. Build it again.</p>
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
          {rendering ? "Building…" : last ? "Rebuild reel" : "Build reel"}
        </button>

        {steps.length > 0 && (
          <ol className="reel-steps" aria-label="Reel run steps">
            {steps.map((s) => (
              <li key={s.key} data-status={s.status}>
                <span className="reel-step-icon" aria-hidden>
                  {s.status === "running" ? <span className="spinner spinner-sm" /> : s.status === "ok" ? <Check size={12} /> : <X size={12} />}
                </span>
                <span className="reel-step-name">{toolLabel(s.tool)}</span>
                <span className="reel-step-dur num">{s.result ? duration(s.result.ms) : ""}</span>
                {s.result?.summary && <span className="reel-step-summary">{s.result.summary}</span>}
              </li>
            ))}
          </ol>
        )}
        {trace?.messages.slice(-1).map((m, i) => (
          <div key={i} className="reel-message">
            <MessageText text={m} />
          </div>
        ))}
        {last && (
          <button
            className="link link-quiet"
            onClick={() => {
              setRun(last.id);
              setTab("trace");
            }}
          >
            Open the full trace
          </button>
        )}
      </div>
    </div>
  );
}
