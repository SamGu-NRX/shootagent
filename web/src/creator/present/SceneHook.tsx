// Scene 1, "The problem": a wall of the real stock lines from the cliché corpus, the template reel playing in
// front of it, the lines it repeats lighting up, then the headline: every listing reel sounds the same.

import "../../styles/present-hook.css";
import { LayoutTemplate, Quote, Timer } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { LabState } from "../types";
import { easeInOut, easeOut, phase, type SceneProps } from "./types";

const STAGE_W = 1920;
const STAGE_H = 1080;
const ROW_H = 26;
const ROW_GAP = 7;
const TILE_GAP = 7;
const BLEED = 70;
const PHONE_W = 380;
const PHONE_H = 676;
const PHONE_TOP = (STAGE_H - PHONE_H) / 2;
const PHONE_START = (STAGE_W - PHONE_W) / 2;
const PHONE_END = 1330;
/** ms: when the template reel appears and starts playing */
const REEL_AT = 2200;
const HITS = 10;

// ---- which stock lines the template repeats -----------------------------------------------------------

const STOP = new Set(["the", "and", "for", "with", "this", "that", "your", "you", "unit", "demo", "from", "into", "our"]);

function stem(word: string): string {
  let s = word.toLowerCase().replace(/[^a-z]/g, "");
  if (s.length > 5 && s.endsWith("ing")) s = s.slice(0, -3);
  else if (s.length > 4 && s.endsWith("ed")) s = s.slice(0, -2);
  else if (s.length > 3 && s.endsWith("s")) s = s.slice(0, -1);
  return s;
}

const stems = (text: string) => new Set(text.split(/[^A-Za-z]+/).map(stem).filter((w) => w.length >= 3 && !STOP.has(w)));

/** Every word the template reel puts on screen: hook, room labels, stats card, end card. */
function templateWords(lab: LabState): Set<string> {
  const c = lab.concepts.find((x) => x.agent === "template");
  if (!c) return new Set();
  const texts = [c.hook.text, c.ending.text, ...c.beats.flatMap((b) => [b.text ?? "", b.card?.text ?? ""])];
  return new Set(texts.flatMap((x) => [...stems(x)]));
}

// ---- the wall -----------------------------------------------------------------------------------------

interface Tile {
  i: number;
  label: string;
  /** 0-based order in which it lights up red, or -1 */
  hit: number;
  /** ms */
  delay: number;
  row: number;
}

/** Deterministic shuffle so the same lab always builds the same wall. */
function shuffled<T>(xs: T[], seed = 7): T[] {
  const out = [...xs];
  let s = seed;
  for (let i = out.length - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) % 2147483648;
    const j = s % (i + 1);
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}

const tileWidth = (label: string) => label.length * 6.7 + 24;

function buildWall(lab: LabState): { rows: Tile[][]; hits: number } {
  const labels = (lab.evaluation?.map?.points ?? []).filter((p) => p.kind === "cliche").map((p) => p.label);
  const words = templateWords(lab);
  const scored = labels
    .map((label, i) => {
      const own = stems(label);
      let n = 0;
      for (const w of own) if (words.has(w)) n++;
      return { i, n };
    })
    .filter((x) => x.n > 0)
    .sort((a, b) => b.n - a.n || a.i - b.i)
    .slice(0, HITS);
  const hitOrder = new Map(scored.map((x, k) => [x.i, k]));

  // pack into rows a little wider than the stage, so the wall bleeds off both edges
  const rows: { i: number; label: string }[][] = [[]];
  let width = 0;
  for (const i of shuffled(labels.map((_, k) => k))) {
    const w = tileWidth(labels[i]!) + TILE_GAP;
    if (width + w > STAGE_W + 2 * BLEED && rows[rows.length - 1]!.length) {
      rows.push([]);
      width = 0;
    }
    rows[rows.length - 1]!.push({ i, label: labels[i]! });
    width += w;
  }

  // the wall fills from the centre outwards, with a little jitter
  const top = (STAGE_H - (rows.length * (ROW_H + ROW_GAP) - ROW_GAP)) / 2;
  const maxDist = Math.hypot(STAGE_W / 2 + BLEED, STAGE_H / 2);
  let jitter = 11;
  const out = rows.map((row, r) => {
    const rowW = row.reduce((a, x) => a + tileWidth(x.label) + TILE_GAP, -TILE_GAP);
    let x = (STAGE_W - rowW) / 2;
    const y = top + r * (ROW_H + ROW_GAP) + ROW_H / 2;
    return row.map((tile) => {
      const w = tileWidth(tile.label);
      const cx = x + w / 2;
      x += w + TILE_GAP;
      jitter = (jitter * 48271) % 2147483647;
      const dist = Math.hypot(cx - STAGE_W / 2, y - STAGE_H / 2) / maxDist;
      return { i: tile.i, label: tile.label, hit: hitOrder.get(tile.i) ?? -1, delay: 80 + dist * 1900 + (jitter % 260), row: r };
    });
  });
  return { rows: out, hits: scored.length };
}

// grey tile -> red tile
const mix = (a: number, b: number, k: number) => Math.round(a + (b - a) * k);

// ---- scene --------------------------------------------------------------------------------------------

export function SceneHook({ lab, story, fileUrl, t, playing }: SceneProps) {
  const { rows, hits } = useMemo(() => buildWall(lab), [lab]);
  const reel = lab.reels.find((r) => r.kind === "baseline");
  const total = rows.reduce((n, r) => n + r.length, 0);

  // the template reel plays from the moment it appears, kept in step with the scene clock
  const video = useRef<HTMLVideoElement>(null);
  const reelTime = Math.max(0, (t - REEL_AT) / 1000);
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const want = playing && t >= REEL_AT;
    if (v.readyState >= 1 && Math.abs(v.currentTime - reelTime) > 0.35) v.currentTime = Math.min(reelTime, Math.max(0, (v.duration || 1e9) - 0.05));
    if (want && v.paused) void v.play().catch(() => undefined);
    if (!want && !v.paused) v.pause();
  }, [t, playing, reelTime]);

  const dim = phase(t, 4000, 800, easeInOut);
  const phoneIn = phase(t, REEL_AT, 700, easeOut);
  const slide = phase(t, 4000, 900, easeInOut);
  const drift = phase(t, 0, 7000, (x) => x);
  const lineA = phase(t, 4300, 650, easeOut);
  const lineB = phase(t, 4800, 650, easeOut);
  const kicker = phase(t, 4150, 500, easeOut);
  const chip = phase(t, 5500, 600, easeOut);
  const note = phase(t, 1500, 600, easeOut);
  const scrolledPast = story.template ? 1 - story.template.stopRate : null;
  const shownPct = scrolledPast != null ? Math.round(scrolledPast * 100 * phase(t, 5600, 700, easeOut)) : 0;

  return (
    <div className="pz-hook">
      <div className="pz-hook-wall" style={{ transform: `scale(${1.05 - 0.05 * phase(t, 0, 7000, easeOut)})` }} aria-hidden>
        {rows.map((row, r) => (
          <div key={r} className="pz-hook-row" style={{ transform: `translateX(${(r % 2 ? -1 : 1) * (drift * 34 - 17)}px)` }}>
            {row.map((tile) => {
              const a = phase(t, tile.delay, 300, easeOut);
              const h = tile.hit >= 0 ? phase(t, 2900 + tile.hit * 120, 320, easeOut) : 0;
              const base = 0.88 - (tile.hit >= 0 ? 0.12 : 0.72) * dim;
              return (
                <span
                  key={tile.i}
                  className="pz-hook-tile"
                  data-hit={h > 0 || undefined}
                  style={{
                    opacity: a * (h > 0 ? Math.max(base, 0.95) : base),
                    transform: `scale(${0.86 + 0.14 * a + 0.04 * h * (1 - dim)})`,
                    color: `rgb(${mix(138, 255, h)} ${mix(138, 140, h)} ${mix(146, 130, h)})`,
                    background: `rgb(${mix(255, 255, h)} ${mix(255, 107, h)} ${mix(255, 94, h)} / ${0.045 + 0.1 * h})`,
                    borderColor: `rgb(${mix(255, 255, h)} ${mix(255, 107, h)} ${mix(255, 94, h)} / ${0.07 + 0.38 * h})`,
                  }}
                >
                  {tile.label}
                </span>
              );
            })}
          </div>
        ))}
      </div>

      <div className="pz-hook-scrim" style={{ opacity: dim }} aria-hidden />

      <p className="pz-hook-corpus" style={{ opacity: note, transform: `translateY(${(1 - note) * 10}px)` }}>
        <Quote size={18} aria-hidden />
        <span>
          <b className="num">{total}</b> stock lines from real listing reels
          <span className="pz-hook-corpus-dim"> · a cliché corpus the agents never see</span>
        </span>
      </p>

      {reel && (
        <figure
          className="pz-hook-phone"
          style={{
            left: PHONE_START + (PHONE_END - PHONE_START) * slide,
            top: PHONE_TOP,
            opacity: phoneIn,
            transform: `translateY(${(1 - phoneIn) * 26}px) scale(${0.94 + 0.06 * phoneIn})`,
          }}
        >
          <div className="pz-hook-screen">
            <video ref={video} src={fileUrl(reel.video)} poster={fileUrl(reel.poster)} muted playsInline preload="auto" />
          </div>
          <figcaption>
            <LayoutTemplate size={20} aria-hidden /> A typical listing reel
            {hits > 0 && (
              <span className="pz-hook-hits" style={{ opacity: phase(t, 3400, 500, easeOut) }}>
                <span className="num">{hits}</span> stock lines matched
              </span>
            )}
          </figcaption>
        </figure>
      )}

      <div className="pz-hook-copy">
        <p className="pz-kicker pz-hook-kicker" style={{ opacity: kicker, transform: `translateY(${(1 - kicker) * 12}px)` }}>
          The problem
        </p>
        <h1 className="pz-hook-title">
          <span style={{ opacity: lineA, transform: `translateY(${(1 - lineA) * 30}px)` }}>
            Every listing reel
            <br />
            sounds the same.
          </span>
          <span className="pz-hook-title-dim" style={{ opacity: lineB, transform: `translateY(${(1 - lineB) * 30}px)` }}>
            Buyers scroll past
            <br />
            in 1.5 seconds.
          </span>
        </h1>
        {scrolledPast != null && (
          <div className="pz-hook-chip" style={{ opacity: chip, transform: `translateY(${(1 - chip) * 16}px)` }}>
            <Timer size={30} aria-hidden />
            <div>
              <p>
                <b className="num">{shownPct}%</b> of viewers scrolled past the template in 1.5 s
              </p>
              <p className="pz-hook-chip-note">AI viewer panel · {story.viewers} simulated viewers</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
