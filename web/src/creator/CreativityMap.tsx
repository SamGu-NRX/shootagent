import { useMemo, useState } from "react";
import { useCreator } from "./store";
import type { MapPoint } from "./types";
import { onDark, reelColors, useAgents, useWidth } from "./ui";

const H = 380;
const PAD = 24;

/** 2-sigma covariance ellipse of a point cloud, in the same coordinates. */
export function cloudEllipse(pts: { x: number; y: number }[], k = 2) {
  const n = pts.length || 1;
  const cx = pts.reduce((a, p) => a + p.x, 0) / n;
  const cy = pts.reduce((a, p) => a + p.y, 0) / n;
  const sxx = pts.reduce((a, p) => a + (p.x - cx) ** 2, 0) / n;
  const syy = pts.reduce((a, p) => a + (p.y - cy) ** 2, 0) / n;
  const sxy = pts.reduce((a, p) => a + (p.x - cx) * (p.y - cy), 0) / n;
  const tr = sxx + syy;
  const disc = Math.sqrt(Math.max(0, (tr * tr) / 4 - (sxx * syy - sxy * sxy)));
  const l1 = tr / 2 + disc;
  const l2 = Math.max(0, tr / 2 - disc);
  const angle = sxy !== 0 ? (Math.atan2(l1 - sxx, sxy) * 180) / Math.PI : sxx >= syy ? 0 : 90;
  return { cx, cy, rx: k * Math.sqrt(l1), ry: k * Math.sqrt(l2), angle };
}

export function CreativityMap() {
  const map = useCreator((s) => s.lab?.evaluation?.map);
  const novelty = useCreator((s) => s.lab?.evaluation?.novelty);
  const reels = useCreator((s) => s.lab?.reels);
  const concepts = useCreator((s) => s.lab?.concepts);
  const open = useCreator((s) => s.openConcept);
  const agents = useAgents();
  const [hover, setHover] = useState<MapPoint | null>(null);
  const [box, W] = useWidth<HTMLDivElement>(620);

  const geo = useMemo(() => {
    const pts = map?.points ?? [];
    if (!pts.length) return null;
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    const sx = (x: number) => PAD + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * PAD);
    const sy = (y: number) => H - PAD - ((y - y0) / (y1 - y0 || 1)) * (H - 2 * PAD);
    const at = new Map(pts.map((p) => [p.id, { x: sx(p.x), y: sy(p.y) }]));
    const cliches = pts.filter((p) => p.kind === "cliche").map((p) => at.get(p.id)!);
    return { sx, sy, at, zone: cliches.length > 2 ? cloudEllipse(cliches) : null };
  }, [map, W]);

  if (!map || !geo) {
    return (
      <div className="cl-panel cl-map">
        <div className="cl-panel-head">
          <h3 className="cl-label">Creativity map</h3>
        </div>
        <div className="cl-map-plot" ref={box}>
          <p className="cl-empty-line">The map appears once every concept has been scored for novelty.</p>
        </div>
      </div>
    );
  }

  const colors = reelColors(reels ?? []);
  const reelOf = new Map((reels ?? []).map((r) => [r.concept, r]));
  const nov = new Map((novelty ?? []).filter((n) => n.kind === "concept").map((n) => [n.id, n]));
  const pts = map.points;
  const cliches = pts.filter((p) => p.kind === "cliche");
  const order: Record<MapPoint["kind"], number> = { cliche: 0, concept: 1, single_agent: 2, baseline: 3, finalist: 4 };
  const marks = pts.filter((p) => p.kind !== "cliche").sort((a, b) => order[a.kind] - order[b.kind]);
  const lineage = (concepts ?? []).flatMap((c) => c.parents.filter((p) => geo.at.has(p) && geo.at.has(c.id)).map((p) => [geo.at.get(p)!, geo.at.get(c.id)!] as const));
  const hp = hover ? geo.at.get(hover.id) : null;
  const hn = hover ? nov.get(hover.id) : undefined;
  const who = hover?.agent ? agents(hover.agent) : null;
  const kindText: Record<MapPoint["kind"], string> = {
    cliche: "Stock listing phrase",
    concept: "Room concept",
    single_agent: "Single-agent concept",
    finalist: "Finalist",
    baseline: "Template",
  };

  return (
    <div className="cl-panel cl-map">
      <div className="cl-panel-head">
        <h3 className="cl-label">Creativity map</h3>
        <span className="cl-panel-count">{map.method}</span>
      </div>
      <div className="cl-map-plot" ref={box}>
        <svg width={W} height={H} role="img" aria-label={`Creativity map: ${marks.length} concepts and ${cliches.length} stock listing phrases placed by meaning.`}>
          {geo.zone && (
            <g className="cl-map-zone">
              <ellipse cx={geo.zone.cx} cy={geo.zone.cy} rx={geo.zone.rx} ry={geo.zone.ry} transform={`rotate(${geo.zone.angle} ${geo.zone.cx} ${geo.zone.cy})`} />
              <text x={geo.zone.cx} y={Math.max(14, geo.zone.cy - Math.max(geo.zone.rx, geo.zone.ry) * 0.62)} textAnchor="middle">
                the cliche zone
              </text>
            </g>
          )}
          {cliches.map((p) => {
            const a = geo.at.get(p.id)!;
            return (
              <g key={p.id} className="cl-map-cliche" onPointerEnter={() => setHover(p)} onPointerLeave={() => setHover(null)}>
                <circle cx={a.x} cy={a.y} r={2.6} />
                <circle cx={a.x} cy={a.y} r={7} className="cl-map-hit" />
              </g>
            );
          })}
          {lineage.map(([a, b], i) => (
            <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="cl-map-lineage" />
          ))}
          {marks.map((p) => {
            const a = geo.at.get(p.id)!;
            const color = p.agent ? onDark(agents(p.agent).color) : "#bdbdbd";
            const reel = reelOf.get(p.id);
            const ring = reel ? colors[reel.id] : undefined;
            const label = `${p.label}. ${kindText[p.kind]}${p.agent ? ` by ${agents(p.agent).name}` : ""}.`;
            return (
              <g
                key={p.id}
                className="cl-map-pt"
                data-kind={p.kind}
                tabIndex={0}
                role="button"
                aria-label={label}
                onPointerEnter={() => setHover(p)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(p)}
                onBlur={() => setHover(null)}
                onClick={() => open(p.id)}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open(p.id))}
              >
                <circle cx={a.x} cy={a.y} r={13} className="cl-map-hit" />
                {p.kind === "baseline" ? (
                  <rect x={a.x - 6} y={a.y - 6} width={12} height={12} rx={1.5} style={{ fill: ring ?? "#c9c9c9" }} />
                ) : p.kind === "single_agent" ? (
                  <circle cx={a.x} cy={a.y} r={5.5} className="cl-map-hollow" style={ring ? { stroke: ring } : undefined} />
                ) : (
                  <circle cx={a.x} cy={a.y} r={p.kind === "finalist" ? 6.5 : 5.5} style={{ fill: color }} className="cl-map-dot" />
                )}
                {ring && p.kind !== "baseline" && <circle cx={a.x} cy={a.y} r={11} className="cl-map-ring" style={{ stroke: ring }} />}
                {reel && (
                  <text x={a.x > W - 230 ? a.x - 16 : a.x + 16} y={a.y + 4} textAnchor={a.x > W - 230 ? "end" : "start"} className="cl-map-label">
                    {reel.id} · {p.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover && hp && (
          <div className="cl-map-tip" style={{ left: hp.x, top: hp.y }} data-flip={hp.x > W * 0.6 || undefined} role="status">
            <p className="cl-map-tip-kind">
              {who && <span className="cl-map-tip-dot" style={{ background: onDark(who.color) }} />}
              {kindText[hover.kind]}
              {who && hover.kind !== "baseline" ? ` · ${who.name}` : ""}
              {hover.round ? ` · round ${hover.round}` : ""}
            </p>
            <p className="cl-map-tip-title">{hover.kind === "cliche" ? `“${hover.label}”` : hover.label}</p>
            {hn && (
              <p className="cl-map-tip-near">
                Closest cliche <q>{hn.nearest_cliche}</q> <span className="num">({hn.cliche_similarity.toFixed(2)})</span>
                <span className="cl-map-tip-nov num"> · novelty {hn.novelty.toFixed(2)}</span>
              </p>
            )}
          </div>
        )}
      </div>
      <ul className="cl-map-legend">
        <li>
          <svg width="12" height="12" aria-hidden>
            <circle cx="6" cy="6" r="2.6" className="cl-map-legend-cliche" />
          </svg>
          Stock phrase
        </li>
        <li>
          <svg width="12" height="12" aria-hidden>
            <circle cx="6" cy="6" r="5" fill="#e9a53b" />
          </svg>
          Room concept, by creator
        </li>
        <li>
          <svg width="22" height="22" aria-hidden>
            <circle cx="11" cy="11" r="5.5" fill="#8f80f0" />
            <circle cx="11" cy="11" r="9.5" fill="none" stroke="#8f80f0" strokeWidth="1.5" />
          </svg>
          Finalist
        </li>
        <li>
          <svg width="12" height="12" aria-hidden>
            <circle cx="6" cy="6" r="4.5" fill="none" stroke="#bdbdbd" strokeWidth="2" />
          </svg>
          Single agent
        </li>
        <li>
          <svg width="12" height="12" aria-hidden>
            <rect x="1" y="1" width="10" height="10" rx="1.5" fill="#c9c9c9" />
          </svg>
          Template
        </li>
      </ul>
      <p className="cl-foot">Placed by meaning: closer dots say more similar things. Lines join a remix to the concepts it came from. Hover a dot for its closest cliche.</p>
    </div>
  );
}
