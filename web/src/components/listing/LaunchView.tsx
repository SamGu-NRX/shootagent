import { ArrowLeft, FileText, Inbox, QrCode, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { FilmState, ListingState } from "../../api/listing";
import { buildStoryboard, latestFilmTrace } from "../../listing/storyboard";
import { useListingApi } from "../../state/listingApi";
import { useStore } from "../../state/store";
import { radioKeys } from "../radioKeys";
import { TracePanel } from "../TracePanel";
import { FilmPlayer, type FilmPlayerHandle } from "./FilmPlayer";
import { PublishDialog } from "./PublishDialog";
import { Storyboard } from "./Storyboard";

type FilmLoad = { state: "loading" } | { state: "ready"; film: FilmState } | { state: "none" } | { state: "error"; message: string };

const LANG_NAMES: Record<string, string> = { en: "English", es: "Español" };

function useNarrow(query: string) {
  const [match, setMatch] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const on = () => setMatch(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Desktop "Launch listing": storyboard, the film, the live trace, and Publish. */
export function LaunchView({ shootId }: { shootId: string }) {
  const api = useListingApi();
  const openShoot = useStore((s) => s.openShoot);
  const closeShoot = useStore((s) => s.closeShoot);
  const storeApi = useStore((s) => s.api);
  const shoot = useStore((s) => s.shoot);
  const runs = useStore((s) => s.runs);
  const traces = useStore((s) => s.traces);
  const setTraceRun = useStore((s) => s.setTraceRun);
  const shootError = useStore((s) => s.shootError);

  const [film, setFilm] = useState<FilmLoad>({ state: "loading" });
  const [state, setState] = useState<ListingState | null>(null);
  const [lang, setLang] = useState("en");
  const [current, setCurrent] = useState(0);
  const [publishing, setPublishing] = useState(false);
  const [making, setMaking] = useState<string | null>(null);
  const [tab, setTab] = useState<"story" | "trace">("story");
  const player = useRef<FilmPlayerHandle>(null);
  const narrow = useNarrow("(max-width: 1279px)");

  useEffect(() => {
    void openShoot(shootId);
    return closeShoot;
  }, [shootId, openShoot, closeShoot, storeApi]);

  const loadFilm = useCallback(async () => {
    try {
      const f = await api.getFilm(shootId);
      setFilm(f.manifest ? { state: "ready", film: f } : { state: "none" });
      if (f.manifest) {
        const langs = Object.keys(f.variants);
        setLang((l) => (langs.includes(l) ? l : (langs[0] ?? "en")));
      }
    } catch (e) {
      // 404 from a server without the film routes reads the same as "no film yet"
      if (e instanceof ApiError && e.status === 404) setFilm({ state: "none" });
      else setFilm({ state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [api, shootId]);

  useEffect(() => {
    void loadFilm();
    api.getState(shootId).then(setState, () => setState(null));
  }, [api, shootId, loadFilm]);

  const filmRuns = useMemo(() => runs.filter((r) => r.mode === "film" || traces[r.id]?.mode === "film").map((r) => r.id), [runs, traces]);
  const filmTrace = latestFilmTrace(traces, filmRuns);
  const liveFilm = filmTrace?.status === "running";

  // show the film run in the trace, and reload the film when a run finishes
  useEffect(() => {
    const last = filmRuns[filmRuns.length - 1];
    if (last) setTraceRun(last);
  }, [filmRuns, setTraceRun]);
  const wasLive = useRef(false);
  useEffect(() => {
    if (wasLive.current && !liveFilm) void loadFilm();
    wasLive.current = liveFilm;
  }, [liveFilm, loadFilm]);

  const shots = useMemo(() => (film.state === "ready" ? buildStoryboard(film.film, filmTrace) : []), [film, filmTrace]);
  const viewLabel = (id: string) => shoot?.views.find((v) => v.id === id)?.label || id;
  const photoUrl = (blob: string, w: number) => storeApi.blobUrl(shootId, blob, w);

  const makeFilm = async () => {
    setMaking(null);
    try {
      const { run_id } = await api.makeFilm(shootId, ["en", "es"]);
      const st = useStore.getState();
      useStore.setState({ runs: [...st.runs, { id: run_id, mode: "film", started: new Date().toISOString(), done: false, usd: 0, summary: null }] });
      await st.refreshShoot();
      void openShoot(shootId);
    } catch (e) {
      setMaking(e instanceof Error ? e.message : String(e));
    }
  };

  if (shootError) {
    return (
      <div className="launch launch-error">
        <div className="empty-state" role="alert">
          <h3>Couldn't open this shoot</h3>
          <p>{shootError.message}</p>
          <a className="btn" href="#/">Back to shoots</a>
        </div>
      </div>
    );
  }

  const ready = film.state === "ready" ? film.film : null;
  const langs = ready ? Object.keys(ready.variants) : [];
  const variant = ready?.variants[lang];
  const generated = shots.filter((s) => s.aiLabel).length;
  const rejected = shots.reduce((n, s) => n + s.rejected.length, 0);
  const duration = variant?.duration_s ?? ready?.manifest?.languages[lang]?.duration_s ?? null;

  const story = (
    <section className="launch-story" aria-labelledby="story-title">
      <header className="pane-head">
        <h2 id="story-title" className={narrow ? "sr-only" : undefined}>
          Storyboard
        </h2>
        {ready && (
          <p className="pane-sub num">
            {shots.length} shots
            {duration != null && <> · {duration.toFixed(1)} s</>}
            {rejected > 0 && <> · {rejected} generated clip{rejected > 1 ? "s" : ""} rejected</>}
            {` · ${generated} AI-labeled`}
          </p>
        )}
      </header>
      {film.state === "loading" && <div className="pane-empty"><span className="spinner" aria-label="Loading the film" /></div>}
      {film.state === "error" && (
        <div className="pane-empty" role="alert">
          <p>{film.message}</p>
          <button className="btn" onClick={() => void loadFilm()}>Retry</button>
        </div>
      )}
      {film.state === "none" && (
        <div className="pane-empty">
          <h3>{liveFilm ? "The agent is making the film" : "No film yet"}</h3>
          <p>
            {liveFilm
              ? "Shots appear here once it's assembled. Follow each clip and check in the trace."
              : "The agent plans the shots, checks every generated clip against its photo, narrates in English and Spanish, and cuts a 9:16 film."}
          </p>
          {!liveFilm && (
            <button className="btn btn-primary" onClick={makeFilm}>
              Make the film
            </button>
          )}
          {making && <p className="form-error" role="alert">{making}</p>}
        </div>
      )}
      {ready && (
        <Storyboard
          shots={shots}
          lang={lang}
          current={current}
          photoUrl={photoUrl}
          viewLabel={viewLabel}
          onPick={(i) => {
            setCurrent(i);
            const t = shots[i]?.times[lang]?.[0];
            if (t != null) player.current?.seek(t);
          }}
        />
      )}
    </section>
  );

  const trace = (
    <aside className="launch-trace" aria-label="Agent trace">
      {!narrow && (
        <header className="pane-head">
          <h2>Agent trace</h2>
        </header>
      )}
      <div className="launch-trace-body">
        {runs.length ? (
          <TracePanel />
        ) : (
          <div className="pane-empty">
            <p>The film run's plan, every clip check and each fallback show up here as the agent works.</p>
          </div>
        )}
      </div>
    </aside>
  );

  return (
    <div className="launch">
      <header className="launch-top">
        <a className="icon-btn" href={`#/s/${encodeURIComponent(shootId)}`} aria-label="Back to the shoot">
          <ArrowLeft size={16} />
        </a>
        <div className="launch-title">
          <h1 tabIndex={-1}>Launch listing</h1>
          <p className="ws-sub">{shoot?.listing.address || shoot?.title || " "}</p>
        </div>
        {state && (
          <span className="status-pill" data-live={state.published || undefined}>
            <span className="status-dot" aria-hidden />
            {state.published ? "Live" : "Draft"}
            {state.leads.new > 0 && <span className="num"> · {state.leads.new} new lead{state.leads.new > 1 ? "s" : ""}</span>}
          </span>
        )}
        <nav className="launch-actions" aria-label="Listing">
          <a className="btn btn-quiet" href={`#/s/${encodeURIComponent(shootId)}/disclosure`}>
            <FileText size={14} aria-hidden /> Disclosure
          </a>
          <a className="btn btn-quiet" href="#/r">
            <Inbox size={14} aria-hidden /> Leads
          </a>
          <button
            className={state?.published || film.state !== "ready" ? "btn" : "btn btn-primary"}
            onClick={() => setPublishing(true)}
            disabled={!shoot || !state}
          >
            {state?.published ? <QrCode size={14} aria-hidden /> : <Upload size={14} aria-hidden />}
            {state?.published ? "Show QR code" : "Publish listing"}
          </button>
        </nav>
      </header>

      <main className="launch-body" data-narrow={narrow || undefined}>
        <section className="launch-player" aria-label="Film">
          {ready && langs.length > 0 ? (
            <>
              {langs.length > 1 && (
                <div className="seg" role="radiogroup" aria-label="Film language" onKeyDown={radioKeys}>
                  {langs.map((l) => (
                    <button key={l} role="radio" aria-checked={lang === l} tabIndex={lang === l ? 0 : -1} className="seg-btn" onClick={() => setLang(l)}>
                      {LANG_NAMES[l] ?? l}
                    </button>
                  ))}
                </div>
              )}
              <FilmPlayer
                ref={player}
                variant="desk"
                lang={lang}
                url={variant?.url || api.filmUrl(shootId, lang)}
                poster={variant?.cover ?? null}
                shots={shots}
                frameFor={(i) => (shots[i] ? photoUrl(shots[i]!.photo, 1080) : null)}
                onShot={setCurrent}
                labels={{ film: "Listing film", play: "Play", pause: "Pause", sound: "Sound on", mute: "Sound off" }}
              />
              <div className="disclose" role="note">
                <span className="disclose-tag" data-on={generated > 0 || undefined}>AB 723</span>
                <p>
                  {generated > 0
                    ? `${generated} shot${generated > 1 ? "s carry" : " carries"} the on-screen label “${ready.manifest?.disclosure.label[lang] ?? "AI-generated motion"}”.`
                    : "No AI-generated motion: every shot moves through the photo's own pixels, so no label is needed."}
                </p>
              </div>
              {state && (
                <p className="review-line" data-ok={state.film_approved || undefined}>
                  {state.film_approved ? "Approved by the listing agent" : "Waiting for the listing agent's approval"}
                  {state.note && <span className="review-note">“{state.note}”</span>}
                </p>
              )}
            </>
          ) : (
            <div className="player-empty" aria-hidden>
              <span>9:16</span>
            </div>
          )}
        </section>

        {narrow ? (
          <section className="launch-tabs">
            <div
              className="tabs"
              role="tablist"
              aria-label="Launch"
              onKeyDown={(e) => {
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                e.preventDefault();
                const next = tab === "story" ? "trace" : "story";
                setTab(next);
                document.getElementById(`launch-tab-${next}`)?.focus();
              }}
            >
              {(["story", "trace"] as const).map((id) => (
                <button
                  key={id}
                  id={`launch-tab-${id}`}
                  role="tab"
                  className="tab"
                  aria-selected={tab === id}
                  aria-controls="launch-tabpanel"
                  tabIndex={tab === id ? 0 : -1}
                  onClick={() => setTab(id)}
                >
                  {id === "story" ? "Storyboard" : "Trace"}
                  {id === "trace" && liveFilm && <span className="live-dot" aria-hidden />}
                  {id === "trace" && liveFilm && <span className="sr-only"> (running)</span>}
                </button>
              ))}
            </div>
            <div id="launch-tabpanel" role="tabpanel" aria-labelledby={`launch-tab-${tab}`} className="launch-tabpanel">
              {tab === "story" ? story : trace}
            </div>
          </section>
        ) : (
          <>
            {story}
            {trace}
          </>
        )}
      </main>

      {publishing && shoot && state && (
        <PublishDialog shootId={shootId} listing={shoot.listing} state={state} onState={setState} onClose={() => setPublishing(false)} />
      )}
    </div>
  );
}
