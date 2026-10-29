import { ChevronRight } from "lucide-react";
import type { StoryShot } from "../../listing/storyboard";
import { MOTION_LABEL, MOTION_NOTE } from "../../listing/reasons";

interface Props {
  shots: StoryShot[];
  lang: string;
  current: number;
  photoUrl: (blob: string, w: number) => string;
  viewLabel: (view: string) => string;
  onPick: (index: number) => void;
}

const span = (t?: [number, number]) => (t ? `${fmt(t[0])}–${fmt(t[1])}` : "");
const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/**
 * The film as a contact sheet: one row per shot, in play order. Rejected generated clips stay on the sheet,
 * crossed out the way an editor marks a contact sheet, with the reason beside them and what replaced them.
 */
export function Storyboard({ shots, lang, current, photoUrl, viewLabel, onPick }: Props) {
  return (
    <ol className="sb" aria-label="Shots">
      {shots.map((s) => {
        const label = s.labels[lang] ?? Object.values(s.labels)[0] ?? viewLabel(s.view);
        const plays = s.source ?? s.planned;
        return (
          <li key={s.index} className="sb-row" data-current={s.index === current || undefined}>
            <button
              className="sb-pick"
              onClick={() => onPick(s.index)}
              aria-label={`Play shot ${s.index + 1}, ${label}`}
              aria-current={s.index === current ? "true" : undefined}
            >
              <span className="sb-num num">{String(s.index + 1).padStart(2, "0")}</span>
              <span className="sb-photo">
                <img src={photoUrl(s.photo, 360)} alt="" loading={s.index < 3 ? "eager" : "lazy"} decoding="async" />
                {s.endView && <span className="sb-photo-end">to {viewLabel(s.endView)}</span>}
              </span>
            </button>
            <div className="sb-body">
              <div className="sb-head">
                <h3 className="sb-label">{label}</h3>
                <span className="sb-time num">{span(s.times[lang])}</span>
              </div>
              <p className="sb-line">
                <span className="sb-key">Plays</span>
                <span>
                  <span className="motion" data-source={plays}>
                    {MOTION_LABEL[plays] ?? plays}
                  </span>{" "}
                  <span className="sb-note">{MOTION_NOTE[plays]}</span>
                </span>
              </p>
              <p className="sb-line">
                <span className="sb-key">Check</span>
                {plays === "generated" ? (
                  <span className="sb-status" data-ok="true">
                    Passed: no change to the room seen{s.aiLabel ? " · labeled “AI-generated motion” on screen" : ""}
                  </span>
                ) : (
                  <span className="sb-status">Not needed: nothing in this shot is invented</span>
                )}
              </p>
              {s.rejected.map((r, i) => {
                // an attempt that never produced a clip (replay has no recording) has nothing to cross out
                const made = !!r.sheet || r.reasons.some((x) => x.kind !== "unavailable");
                return (
                <div className="sb-reject" key={i} data-made={made || undefined}>
                  <span className="sb-sheet" aria-hidden>
                    {made && (
                      <>
                        <img src={photoUrl(r.sheet ?? s.photo, r.sheet ? 640 : 360)} alt="" loading="lazy" decoding="async" />
                        <svg className="pencil-x" viewBox="0 0 100 60" preserveAspectRatio="none">
                          <path d="M6 8 C 30 22, 62 40, 95 54" />
                          <path d="M94 7 C 66 24, 36 41, 5 53" />
                        </svg>
                      </>
                    )}
                  </span>
                  <div className="sb-reject-body">
                    <p className="sb-reject-title">
                      {made ? `Generated clip ${s.rejected.length > 1 ? `${i + 1} ` : ""}rejected` : `Generated clip ${i + 1} not made`}
                      <ChevronRight size={12} aria-hidden />
                      <span>replaced by {(MOTION_LABEL[plays] ?? plays).toLowerCase()}</span>
                    </p>
                    <ul className="sb-reasons">
                      {r.reasons.map((reason) => (
                        <li key={reason.detail} data-kind={reason.kind} title={reason.detail}>
                          {reason.plain}
                        </li>
                      ))}
                    </ul>
                    {r.reasons.some((x) => x.kind === "geometry") && (
                      <details className="sb-detail">
                        <summary>Checker numbers</summary>
                        <pre className="num">{r.reasons.filter((x) => x.kind === "geometry").map((x) => x.detail).join("\n")}</pre>
                      </details>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
