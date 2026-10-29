import { ArrowRight, Check, Mic, Music, ShieldAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { conceptSeconds, ConceptChip, criticMeans, VerdictChip } from "./Room";
import { useCreator } from "./store";
import type { Beat, CardSpec, Motion, ReelConcept, Transition } from "./types";
import { Avatar, fmtScore, focusStyle, ReelBadge, reelColors, useAgents } from "./ui";

const MOTION: Record<Motion, string> = {
  push_in: "Push in",
  pull_out: "Pull out",
  pan_left: "Pan left",
  pan_right: "Pan right",
  tilt_up: "Tilt up",
  tilt_down: "Tilt down",
  punch_in: "Punch in",
  static: "Hold",
};
const TRANSITION: Record<Transition, string> = {
  cut: "cut",
  crossfade: "crossfade",
  whip: "whip pan",
  flash: "flash",
  zoom_through: "zoom through",
  dip_black: "dip to black",
};
const CARD: Record<CardSpec["kind"], string> = {
  none: "",
  price_tag: "Price tag",
  stat_count_up: "Count-up",
  list_reveal: "List reveal",
  big_quote: "Big quote",
  typing_chat: "Chat bubble",
};
const human = (s: string) => s.replace(/_/g, " ");

export function cardText(c: CardSpec): string {
  switch (c.kind) {
    case "price_tag":
    case "stat_count_up":
      return [c.value, c.text].filter(Boolean).join(" · ");
    case "list_reveal":
      return (c.items ?? []).join(" / ");
    default:
      return c.text ?? "";
  }
}

export function ConceptDrawer() {
  const id = useCreator((s) => s.drawer);
  const concept = useCreator((s) => (s.drawer ? s.lab?.concepts.find((c) => c.id === s.drawer) : undefined));
  const close = () => useCreator.getState().openConcept(null);
  const ref = useRef<HTMLDialogElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (concept && !d.open) {
      opener.current = document.activeElement;
      d.showModal();
    } else if (!concept && d.open) d.close();
  }, [concept]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [id]);

  return (
    <dialog
      ref={ref}
      className="cl-drawer"
      aria-labelledby="cl-drawer-title"
      onClose={() => {
        close();
        if (opener.current instanceof HTMLElement) opener.current.focus();
      }}
      onClick={(e) => e.target === ref.current && ref.current.close()}
    >
      {concept && (
        <div className="cl-drawer-inner">
          <DrawerHead c={concept} onClose={() => ref.current?.close()} />
          <div className="cl-drawer-scroll" ref={scroller}>
            <DrawerBody c={concept} />
          </div>
        </div>
      )}
    </dialog>
  );
}

function DrawerHead({ c, onClose }: { c: ReelConcept; onClose: () => void }) {
  const who = useAgents()(c.agent);
  const reels = useCreator((s) => s.lab?.reels);
  const reel = reels?.find((r) => r.concept === c.id);
  const colors = reelColors(reels ?? []);
  return (
    <header className="cl-drawer-head">
      <Avatar who={who} size={34} />
      <div className="cl-drawer-who">
        <p className="cl-drawer-name">{who.name}</p>
        <p className="cl-drawer-meta">
          {c.agent === "template" ? "Baseline" : c.parents.length ? "Remix" : `Round ${c.round}`} · {c.status === "revised" ? "remixed" : c.status} ·{" "}
          <span className="num">{conceptSeconds(c).toFixed(1)} s</span>
        </p>
      </div>
      {reel && (
        <span className="cl-drawer-reel">
          <ReelBadge id={reel.id} color={colors[reel.id]!} /> {reel.label}
        </span>
      )}
      <button className="icon-btn" onClick={onClose} aria-label="Close" autoFocus>
        <X size={16} />
      </button>
    </header>
  );
}

function DrawerBody({ c }: { c: ReelConcept }) {
  const photos = useCreator((s) => s.shoot?.photos);
  const labels = useCreator((s) => s.shoot?.labels);
  const allCritiques = useCreator((s) => s.lab?.critiques);
  const concepts = useCreator((s) => s.lab?.concepts);
  const agents = useAgents();
  const critiques = useMemo(() => (allCritiques ?? []).filter((x) => x.concept === c.id), [allCritiques, c.id]);
  const children = useMemo(() => (concepts ?? []).filter((k) => k.parents.includes(c.id)), [concepts, c.id]);
  const means = criticMeans(critiques);
  let at = 0;
  const starts = c.beats.map((b) => {
    const s = at;
    at += b.seconds;
    return s;
  });

  return (
    <>
      <p className="cl-drawer-kicker">{c.format}</p>
      <h2 id="cl-drawer-title" className="cl-drawer-title">
        {c.title}
      </h2>
      <p className="cl-drawer-logline">{c.logline}</p>

      <figure className="cl-drawer-hook">
        <blockquote>“{c.hook.text}”</blockquote>
        <figcaption>
          Hook · {human(c.hook.style)} text{c.hook.sfx !== "none" ? ` · ${human(c.hook.sfx)}` : ""}
          {c.hook.voiceover ? ` · spoken: “${c.hook.voiceover}”` : " · silent"}
        </figcaption>
      </figure>

      {(c.parents.length > 0 || children.length > 0) && (
        <section className="cl-drawer-sec">
          <h3 className="cl-label">Lineage</h3>
          <div className="cl-lineage">
            {c.parents.length > 0 && (
              <div className="cl-lineage-col">
                {c.parents.map((p) => (
                  <ConceptChip key={p} id={p} />
                ))}
              </div>
            )}
            {c.parents.length > 0 && <ArrowRight size={14} aria-hidden className="cl-lineage-arrow" />}
            <span className="cl-lineage-self">{c.title}</span>
            {children.length > 0 && <ArrowRight size={14} aria-hidden className="cl-lineage-arrow" />}
            {children.length > 0 && (
              <div className="cl-lineage-col">
                {children.map((k) => (
                  <ConceptChip key={k.id} id={k.id} />
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      <section className="cl-drawer-sec">
        <h3 className="cl-label">
          Storyboard · {c.beats.length} shots · <span className="num">{conceptSeconds(c).toFixed(1)} s</span>
        </h3>
        <ol className="cl-beats">
          {c.beats.map((b, i) => (
            <BeatRow key={i} b={b} i={i} start={starts[i]!} photo={photos?.[b.view]} room={labels?.[b.view]} drop={c.music.drop_beat === i} hook={i === 0 ? c.hook.text : null} />
          ))}
          <li className="cl-beat cl-beat-end">
            <div className="cl-beat-frame cl-beat-frame-end">
              <span>{c.ending.text}</span>
            </div>
            <div className="cl-beat-info">
              <p className="cl-beat-time num">Ending · {human(c.ending.style)}</p>
              <p className="cl-beat-onscreen">{c.ending.text}</p>
              {c.ending.voiceover && <p className="cl-beat-vo">“{c.ending.voiceover}”</p>}
            </div>
          </li>
        </ol>
      </section>

      <section className="cl-drawer-sec cl-drawer-grid">
        <div>
          <h3 className="cl-label">
            <Music size={12} aria-hidden /> Music
          </h3>
          <p>
            {c.music.mood}, <span className="num">{c.music.bpm} bpm</span>
            {c.music.drop_beat != null && <>, drops on shot {c.music.drop_beat + 1}</>}
          </p>
        </div>
        <div>
          <h3 className="cl-label">
            <Mic size={12} aria-hidden /> Voice
          </h3>
          <p>{c.voice.voice === "none" ? "No voice: music and on-screen text only" : `${c.voice.voice}${c.voice.direction ? `, ${c.voice.direction}` : ""}`}</p>
        </div>
      </section>

      {(c.caption || c.hashtags.length > 0) && (
        <section className="cl-drawer-sec">
          <h3 className="cl-label">Post caption</h3>
          <p className="cl-caption">{c.caption}</p>
          {c.hashtags.length > 0 && <p className="cl-tags">{c.hashtags.join(" ")}</p>}
        </section>
      )}

      {(c.rationale || c.audience) && (
        <section className="cl-drawer-sec">
          <h3 className="cl-label">Why it stops the scroll</h3>
          {c.rationale && <p>{c.rationale}</p>}
          {c.audience && <p className="muted">For: {c.audience}</p>}
        </section>
      )}

      {c.claims.length > 0 && (
        <section className="cl-drawer-sec">
          <h3 className="cl-label">Claims it makes about the home</h3>
          <ul className="cl-claims">
            {c.claims.map((x) => (
              <li key={x}>
                <Check size={12} aria-hidden /> {x}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="cl-drawer-sec">
        <h3 className="cl-label">Critiques</h3>
        {critiques.length === 0 ? (
          <p className="muted">{c.agent === "template" || c.agent === "single_agent" ? "Not critiqued: it never went through the room." : "No critiques yet."}</p>
        ) : (
          <>
            <p className="cl-crit-means num">
              Mean · originality {fmtScore(+means.originality.toFixed(1))} · hook {fmtScore(+means.hook.toFixed(1))} · specificity {fmtScore(+means.specificity.toFixed(1))} · truth{" "}
              {fmtScore(+means.truth.toFixed(1))} · virality {fmtScore(+means.virality.toFixed(1))}
            </p>
            <ul className="cl-crits">
              {critiques.map((x) => {
                const who = agents(x.critic);
                return (
                  <li key={x.critic} className="cl-crit">
                    <Avatar who={who} size={26} />
                    <div>
                      <p className="cl-crit-head">
                        <b>{who.name}</b> <VerdictChip verdict={x.verdict} />
                        <span className="num cl-crit-scores">
                          {x.originality}/{x.hook}/{x.specificity}/{x.truth}/{x.virality}
                        </span>
                      </p>
                      <p>{x.notes}</p>
                      {x.truth_flags.map((f) => (
                        <p key={f} className="cl-flag" data-tone="truth">
                          <ShieldAlert size={12} aria-hidden /> {f}
                        </p>
                      ))}
                      {x.cliche_flags.map((f) => (
                        <p key={f} className="cl-flag" data-tone="cliche">
                          Cliche: {f}
                        </p>
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

function BeatRow({ b, i, start, photo, room, drop, hook }: { b: Beat; i: number; start: number; photo?: string; room?: string; drop: boolean; hook: string | null }) {
  const onscreen = b.text ?? hook;
  return (
    <li className="cl-beat">
      <div className="cl-beat-frame">
        {photo && <img src={photo} alt="" loading="lazy" style={focusStyle(b.focus)} />}
        {onscreen && (
          <span className="cl-beat-text" data-pos={b.text ? b.text_pos : "center"} data-style={b.text ? b.text_style ?? "label" : "hook"}>
            {onscreen}
          </span>
        )}
        {b.card && b.card.kind !== "none" && <span className="cl-beat-card">{cardText(b.card)}</span>}
      </div>
      <div className="cl-beat-info">
        <p className="cl-beat-time num">
          Shot {i + 1} · {start.toFixed(1)}–{(start + b.seconds).toFixed(1)} s{drop && <span className="cl-beat-drop"> · drop</span>}
        </p>
        <p className="cl-beat-moves">
          {MOTION[b.motion]}
          {room ? ` on the ${room.toLowerCase()}` : ` on ${b.view}`} · {i === 0 ? "opens" : `${TRANSITION[b.transition]} in`}
          {b.sfx !== "none" && ` · ${human(b.sfx)}`}
        </p>
        {b.text && (
          <p className="cl-beat-onscreen">
            {b.text} <span className="muted">({human(b.text_style ?? "label")}, {b.text_pos})</span>
          </p>
        )}
        {!b.text && hook && <p className="cl-beat-onscreen">Hook text over this shot</p>}
        {b.voiceover && <p className="cl-beat-vo">“{b.voiceover}”</p>}
        {b.card && b.card.kind !== "none" && (
          <p className="cl-beat-cardinfo">
            {CARD[b.card.kind]} card: {cardText(b.card)}
          </p>
        )}
      </div>
    </li>
  );
}
