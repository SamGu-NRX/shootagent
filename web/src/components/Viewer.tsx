import { ArrowLeftRight, Layers, Minus, Plus, ScanLine, SquareDashed, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Finding, View } from "../api/types";
import { findingLabel, pct } from "../lib/format";
import { currentView, sourceBlob, sourceLabel, useStore, type CanvasTone, type Source } from "../state/store";
import { radioKeys } from "./radioKeys";
import { SwapImage } from "./SwapImage";
import { useTintedMask } from "./useTintedMask";
import { VersionStrip } from "./VersionStrip";

const PAD = 20;
const MAX_ZOOM = 16;

interface Zoom {
  s: number;
  x: number;
  y: number;
}

const encode = (src: Source) =>
  src.kind === "version" ? `v:${src.id}` : src.kind === "bracket" ? `b:${src.index}` : src.kind;

function decode(value: string): Source {
  if (value.startsWith("v:")) return { kind: "version", id: value.slice(2) };
  if (value.startsWith("b:")) return { kind: "bracket", index: Number(value.slice(2)) };
  return { kind: value as "finished" | "head" | "reference" };
}

function SourceSelect({ view, which, label }: { view: View; which: "a" | "b"; label: string }) {
  const src = useStore((s) => s[which]);
  const setSource = useStore((s) => s.setSource);
  return (
    <label className="src-select">
      <span className="sr-only">{label}</span>
      <select value={encode(src)} onChange={(e) => setSource(which, decode(e.target.value))}>
        <option value="finished">AutoHDR original</option>
        <option value="head">Current ({view.head})</option>
        {view.versions.length > 1 && (
          <optgroup label="Versions">
            {view.versions.map((v) => (
              <option key={v.id} value={`v:${v.id}`}>
                {v.id} · {v.op.replace(/_/g, " ")}
                {v.check ? (v.check.passed ? " ✓" : " ✗") : ""}
              </option>
            ))}
          </optgroup>
        )}
        <optgroup label="Brackets">
          {view.reference && <option value="reference">Bracket fusion</option>}
          {view.brackets.map((b) => (
            <option key={b.index} value={`b:${b.index}`}>
              {sourceLabel(view, { kind: "bracket", index: b.index })}
            </option>
          ))}
        </optgroup>
      </select>
    </label>
  );
}

const TONES: { id: CanvasTone; label: string }[] = [
  { id: "black", label: "Black surround" },
  { id: "dark", label: "Dark grey surround" },
  { id: "mid", label: "18% grey surround" },
];

export function Viewer() {
  const shoot = useStore((s) => s.shoot);
  const view = useStore(currentView);
  const api = useStore((s) => s.api);
  const a = useStore((s) => s.a);
  const b = useStore((s) => s.b);
  const compare = useStore((s) => s.compare);
  const setCompare = useStore((s) => s.setCompare);
  const setSource = useStore((s) => s.setSource);
  const showFindings = useStore((s) => s.showFindings);
  const showMask = useStore((s) => s.showMask);
  const toggle = useStore((s) => s.toggle);
  const tone = useStore((s) => s.canvasTone);
  const setTone = useStore((s) => s.setCanvasTone);
  const maskDraft = useStore((s) => s.maskDraft);
  const attachedMask = useStore((s) => s.attachedMask);
  const addPoint = useStore((s) => s.addMaskPoint);
  const clearDraft = useStore((s) => s.clearMaskDraft);
  const attachDraft = useStore((s) => s.attachDraftMask);
  const instant = useStore((s) => s.instantSwap);
  const selectView = useStore((s) => s.selectView);

  const stageRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState<Zoom>({ s: 1, x: 0, y: 0 });
  const [pos, setPos] = useState(0.5);
  const [failed, setFailed] = useState<string | null>(null);
  const drag = useRef<{ px: number; py: number; z: Zoom; moved: boolean } | null>(null);

  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e!.contentRect.width, h: e!.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [shoot]);

  // New photo: back to fit. Keyboard view changes are instant by design (no animation).
  useEffect(() => {
    setZoom({ s: 1, x: 0, y: 0 });
    setFailed(null);
  }, [view?.id]);

  const metaW = Number(view?.meta.width) || 3;
  const metaH = Number(view?.meta.height) || 2;
  const natural = nat ?? { w: metaW, h: metaH };
  const fit = Math.min((size.w - PAD * 2) / natural.w, (size.h - PAD * 2) / natural.h);
  const bw = Math.max(0, natural.w * fit);
  const bh = Math.max(0, natural.h * fit);

  const clampZoom = useCallback(
    (z: Zoom): Zoom => {
      const s = Math.min(MAX_ZOOM, Math.max(1, z.s));
      const lx = Math.max(0, (bw * s - size.w) / 2 + PAD);
      const ly = Math.max(0, (bh * s - size.h) / 2 + PAD);
      return { s, x: Math.min(lx, Math.max(-lx, z.x)), y: Math.min(ly, Math.max(-ly, z.y)) };
    },
    [bw, bh, size.w, size.h],
  );

  const zoomAt = useCallback(
    (factor: number, px = size.w / 2, py = size.h / 2) => {
      setZoom((z) => {
        const s = Math.min(MAX_ZOOM, Math.max(1, z.s * factor));
        const cx = px - size.w / 2;
        const cy = py - size.h / 2;
        return clampZoom({ s, x: cx - (cx - z.x) * (s / z.s), y: cy - (cy - z.y) * (s / z.s) });
      });
    },
    [size.w, size.h, clampZoom],
  );

  const actualPixels = nat ? nat.w / bw : 1;

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, shoot]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input,textarea,select,[contenteditable=true]") || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "+" || e.key === "=") zoomAt(1.25);
      else if (e.key === "-") zoomAt(0.8);
      else if (e.key === "0") setZoom({ s: 1, x: 0, y: 0 });
      else if (e.key === "1") setZoom(clampZoom({ s: actualPixels, x: 0, y: 0 }));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomAt, clampZoom, actualPixels]);

  // image rectangle in stage coordinates
  const rect = {
    left: size.w / 2 + zoom.x - (bw * zoom.s) / 2,
    top: size.h / 2 + zoom.y - (bh * zoom.s) / 2,
    w: bw * zoom.s,
    h: bh * zoom.s,
  };

  const toImage = (clientX: number, clientY: number) => {
    const r = stageRef.current!.getBoundingClientRect();
    return { x: (clientX - r.left - rect.left) / rect.w, y: (clientY - r.top - rect.top) / rect.h };
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    if (e.shiftKey) {
      const p = toImage(e.clientX, e.clientY);
      if (p.x < 0 || p.y < 0 || p.x > 1 || p.y > 1) return;
      addPoint(+p.x.toFixed(4), +p.y.toFixed(4), e.altKey || e.ctrlKey ? 0 : 1);
      return;
    }
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { px: e.clientX, py: e.clientY, z: zoom, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.px;
    const dy = e.clientY - d.py;
    if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
    setZoom(clampZoom({ s: d.z.s, x: d.z.x + dx, y: d.z.y + dy }));
  };
  const endDrag = () => {
    drag.current = null;
  };
  const onDoubleClick = (e: React.MouseEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    if (zoom.s > 1.01) setZoom({ s: 1, x: 0, y: 0 });
    else zoomAt(Math.max(2, actualPixels), e.clientX - r.left, e.clientY - r.top);
  };

  const aBlob = view ? sourceBlob(view, a) : null;
  const bBlob = view ? sourceBlob(view, b) : null;
  const aUrl = shoot && aBlob ? api.blobUrl(shoot.id, aBlob) : null;
  const bUrl = shoot && bBlob ? api.blobUrl(shoot.id, bBlob) : null;
  const pixelated = rect.w > natural.w * 1.5;

  // mask shown: the draft being clicked, else the one attached to chat, else the mask the B version used
  const maskRecord = useMemo(() => {
    if (!shoot || !view) return null;
    if (maskDraft?.viewId === view.id && maskDraft.mask) return maskDraft.mask;
    if (attachedMask?.view === view.id) return attachedMask;
    const bv = b.kind === "version" ? view.versions.find((v) => v.id === b.id) : view.versions.find((v) => v.id === view.head);
    return bv?.mask ? (shoot.masks[bv.mask] ?? null) : null;
  }, [shoot, view, maskDraft, attachedMask, b]);
  const tinted = useTintedMask(showMask && maskRecord && shoot ? api.blobUrl(shoot.id, maskRecord.blob) : null);

  const boxStyle: React.CSSProperties = {
    width: bw,
    height: bh,
    left: (size.w - bw) / 2,
    top: (size.h - bh) / 2,
    transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.s})`,
  };

  const bracketsOn = a.kind === "reference" || a.kind === "bracket";
  const findings = (view?.findings ?? []).filter((f) => f.region && f.status !== "dismissed");
  const points = maskDraft?.viewId === view?.id ? (maskDraft?.points ?? []) : [];
  const zoomPct = Math.round((rect.w / natural.w) * 100);
  // views regrouping split out of this one: the fix lives there, not in this view's versions
  const children = shoot?.views.filter((v) => view && v.derived_from === view.id) ?? [];

  if (!shoot || !view) {
    return (
      <section className="viewer" aria-label="Photo viewer">
        <div className="viewer-bar" />
        <div className="stage" data-tone={tone}>
          <div className="stage-empty">{shoot ? "This shoot has no photos." : <span className="spinner" aria-label="Loading" />}</div>
        </div>
      </section>
    );
  }

  return (
    <section className="viewer" aria-label="Photo viewer">
      <div className="viewer-bar">
        <div className="vb-group">
          <SourceSelect view={view} which="a" label="Left side (before)" />
          <button
            className="icon-btn"
            aria-label="Swap sides"
            title="Swap sides"
            onClick={() => {
              setSource("a", b);
              setSource("b", a);
            }}
          >
            <ArrowLeftRight size={14} />
          </button>
          <SourceSelect view={view} which="b" label="Right side (after)" />
        </div>
        <div className="seg" role="radiogroup" aria-label="Compare mode" onKeyDown={radioKeys}>
          {(["split", "before", "after"] as const).map((m) => (
            <button key={m} role="radio" aria-checked={compare === m} tabIndex={compare === m ? 0 : -1} className="seg-btn" onClick={() => setCompare(m)}>
              {m === "split" ? "Split" : m === "before" ? "Before" : "After"}
            </button>
          ))}
        </div>
        <div className="vb-group">
          <button
            className="tog"
            aria-label="Brackets"
            aria-pressed={bracketsOn}
            onClick={() => setSource("a", bracketsOn ? { kind: "finished" } : view.reference ? { kind: "reference" } : { kind: "bracket", index: 0 })}
            title="Compare against the exposure brackets"
          >
            <Layers size={14} aria-hidden /> <span className="tog-label">Brackets</span>
          </button>
          <button className="tog" aria-label="Findings" aria-pressed={showFindings} onClick={() => toggle("showFindings")} title="Finding boxes (F)">
            <ScanLine size={14} aria-hidden /> <span className="tog-label">Findings</span>
          </button>
          <button
            className="tog"
            aria-label="Mask"
            aria-pressed={showMask}
            onClick={() => toggle("showMask")}
            title={maskRecord ? "Mask overlay (M)" : "No mask for this version"}
          >
            <SquareDashed size={14} aria-hidden /> <span className="tog-label">Mask</span>
          </button>
        </div>
        <div className="vb-group vb-right">
          <div className="tone-pick" role="radiogroup" aria-label="Surround" onKeyDown={radioKeys}>
            {TONES.map((t) => (
              <button key={t.id} role="radio" aria-checked={tone === t.id} tabIndex={tone === t.id ? 0 : -1} aria-label={t.label} title={t.label} data-tone={t.id} onClick={() => setTone(t.id)} />
            ))}
          </div>
          <div className="zoom-ctl">
            <button className="icon-btn" aria-label="Zoom out" onClick={() => zoomAt(0.8)} disabled={zoom.s <= 1}>
              <Minus size={14} />
            </button>
            <button className="zoom-readout num" onClick={() => setZoom({ s: 1, x: 0, y: 0 })} title="Fit (0) · 100% (1)">
              {zoom.s <= 1.001 ? "Fit" : `${zoomPct}%`}
            </button>
            <button className="icon-btn" aria-label="Zoom in" onClick={() => zoomAt(1.25)} disabled={zoom.s >= MAX_ZOOM}>
              <Plus size={14} />
            </button>
          </div>
        </div>
      </div>

      <div
        ref={stageRef}
        className="stage"
        data-tone={tone}
        data-zoomed={zoom.s > 1.001}
        tabIndex={0}
        aria-label={`${view.label || view.id}. Scroll to zoom, drag to pan, shift-click to mark an object.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={onDoubleClick}
      >
        <div className="layer" hidden={compare === "after"}>
          <div className="imgbox" style={boxStyle}>
            <SwapImage src={aUrl} instant alt={`${view.label}, ${sourceLabel(view, a)}`} pixelated={pixelated} onError={setFailed} />
          </div>
        </div>
        <div
          className="layer"
          hidden={compare === "before"}
          style={compare === "split" ? { clipPath: `inset(0 0 0 ${pos * 100}%)` } : undefined}
        >
          <div className="imgbox" style={boxStyle}>
            <SwapImage src={bUrl} instant={instant} alt={`${view.label}, ${sourceLabel(view, b)}`} pixelated={pixelated} onSize={setNat} onError={setFailed} />
          </div>
        </div>
        {tinted && (
          <div className="layer layer-mask" aria-hidden>
            <div className="imgbox" style={boxStyle}>
              <img src={tinted} alt="" draggable={false} />
            </div>
          </div>
        )}

        <svg className="overlay" width={size.w} height={size.h} aria-hidden>
          {showFindings && findings.map((f) => <CropMarks key={f.id} f={f} rect={rect} />)}
          {points.map(([x, y, label], i) => (
            <g key={i} transform={`translate(${rect.left + x * rect.w} ${rect.top + y * rect.h})`} className="pt" data-label={label}>
              <circle r="7" />
              {label === 1 ? <path d="M-3 0h6M0 -3v6" /> : <path d="M-3 0h6" />}
            </g>
          ))}
        </svg>
        {showFindings &&
          findings.map((f) => {
            const r = f.region as [number, number, number, number];
            const left = Math.max(4, Math.min(size.w - 180, rect.left + r[0] * rect.w));
            // keep tags below the source labels pinned to the top of the stage
            const top = Math.max(38, rect.top + r[1] * rect.h - 24);
            return (
              <span key={f.id} className="finding-tag" data-status={f.status} style={{ transform: `translate(${left}px, ${top}px)` }} title={f.explanation}>
                {findingLabel(f.type)}
                <em>{f.status === "open" ? "open" : f.status === "failed" ? "needs review" : f.status}</em>
              </span>
            );
          })}

        {compare === "split" && (
          <>
            <div className="split-line" style={{ transform: `translateX(${pos * size.w}px)` }} aria-hidden />
            <div
              className="split-handle"
              role="slider"
              tabIndex={0}
              aria-label="Before and after divider"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(pos * 100)}
              style={{ transform: `translate(${pos * size.w}px, ${size.h / 2}px)` }}
              onPointerDown={(e) => {
                e.stopPropagation();
                const el = e.currentTarget;
                el.setPointerCapture(e.pointerId);
                const r = stageRef.current!.getBoundingClientRect();
                const move = (ev: PointerEvent) => setPos(Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)));
                const up = () => {
                  el.removeEventListener("pointermove", move);
                  el.removeEventListener("pointerup", up);
                  el.removeEventListener("pointercancel", up);
                };
                el.addEventListener("pointermove", move);
                el.addEventListener("pointerup", up);
                el.addEventListener("pointercancel", up);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                setPos(0.5);
              }}
              onKeyDown={(e) => {
                const step = e.shiftKey ? 0.01 : 0.05;
                let next: number | null = null;
                if (e.key === "ArrowLeft") next = pos - step;
                else if (e.key === "ArrowRight") next = pos + step;
                else if (e.key === "Home") next = 0;
                else if (e.key === "End") next = 1;
                if (next != null) {
                  e.preventDefault();
                  e.stopPropagation();
                  setPos(Math.min(1, Math.max(0, next)));
                }
              }}
            >
              <span />
            </div>
          </>
        )}

        <div className="stage-labels" aria-hidden>
          {compare !== "after" && <span className="stage-label">{sourceLabel(view, a)}</span>}
          {compare !== "before" && <span className="stage-label stage-label-r">{sourceLabel(view, b)}</span>}
        </div>

        {children.length > 0 && (
          <div className="split-note" onPointerDown={(e) => e.stopPropagation()}>
            <span>This photo mixed {children.length} camera positions. ShootAgent split it into</span>
            {children.map((c) => (
              <button key={c.id} className="btn btn-sm" onClick={() => selectView(c.id)}>
                {c.label || c.id}
              </button>
            ))}
          </div>
        )}

        {failed && (
          <div className="stage-error" role="alert">
            Couldn't load <code>{decodeURIComponent(failed.split("/").pop() ?? failed)}</code>. Check that the server is running, then pick the photo again.
          </div>
        )}

        {maskDraft?.viewId === view.id ? (
          <div className="mask-card" onPointerDown={(e) => e.stopPropagation()}>
            <span className="mask-card-text num">
              {maskDraft.loading
                ? "Segmenting…"
                : maskDraft.error
                  ? maskDraft.error
                  : maskDraft.mask
                    ? `Mask · ${maskDraft.points.length} point${maskDraft.points.length > 1 ? "s" : ""} · ${maskDraft.mask.area != null ? pct(maskDraft.mask.area) : "?"} of frame`
                    : "Mask"}
            </span>
            <button className="btn btn-sm btn-agent" disabled={!maskDraft.mask || maskDraft.loading} onClick={attachDraft}>
              Use in chat
            </button>
            <button className="icon-btn" aria-label="Clear mask" onClick={clearDraft}>
              <X size={14} />
            </button>
          </div>
        ) : (
          <p className="stage-hint">
            <kbd>Shift</kbd>-click an object to mark it for chat · <kbd>Shift</kbd>+<kbd>Alt</kbd>-click to exclude
          </p>
        )}
        {typeof view.meta.request === "string" && view.meta.request.trim() && (
          <p className="stage-request" title="The note the customer sent with this photo">
            <span>Customer request</span>
            {view.meta.request.trim()}
          </p>
        )}
      </div>

      <VersionStrip view={view} />
    </section>
  );
}

function CropMarks({ f, rect }: { f: Finding; rect: { left: number; top: number; w: number; h: number } }) {
  const [x0, y0, x1, y1] = f.region as [number, number, number, number];
  const l = rect.left + x0 * rect.w;
  const t = rect.top + y0 * rect.h;
  const r = rect.left + x1 * rect.w;
  const b = rect.top + y1 * rect.h;
  const k = Math.max(6, Math.min(18, (r - l) / 4, (b - t) / 4));
  const d = [
    `M${l} ${t + k}V${t}H${l + k}`,
    `M${r - k} ${t}H${r}V${t + k}`,
    `M${r} ${b - k}V${b}H${r - k}`,
    `M${l + k} ${b}H${l}V${b - k}`,
  ].join("");
  return (
    <g className="crop" data-status={f.status}>
      <rect x={l} y={t} width={Math.max(0, r - l)} height={Math.max(0, b - t)} className="crop-field" />
      <path d={d} className="crop-shadow" />
      <path d={d} className="crop-mark" />
    </g>
  );
}
