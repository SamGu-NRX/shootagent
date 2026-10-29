// The creator dashboard: the realtor's view of a creative lab. Route #/creator/<shootId>[/<labId>].

import "../styles/creator.css";
import { Clapperboard, Lightbulb, Play, Scale, TrendingUp } from "lucide-react";
import { useEffect } from "react";
import { useStore } from "../state/store";
import { ConceptDrawer } from "./ConceptDrawer";
import { LabHeader, StageBar } from "./Header";
import { LaunchSection } from "./Launch";
import { ReelsSection } from "./Reels";
import { RoomSection } from "./Room";
import { ScorecardSection } from "./Scorecard";
import { creativeApi, useCreator } from "./store";
import { DEFAULT_OPTIONS } from "./types";

export default function CreatorPage({ shootId, labId }: { shootId: string; labId: string | null }) {
  const apiMode = useStore((s) => s.apiMode);
  const lab = useCreator((s) => s.lab);
  const loading = useCreator((s) => s.loading);
  const error = useCreator((s) => s.error);
  const stale = useCreator((s) => s.stale);
  const notice = useCreator((s) => s.notice);
  const labsLoaded = useCreator((s) => s.labsLoaded);

  useEffect(() => {
    void useCreator.getState().open(creativeApi(apiMode), shootId, labId);
  }, [apiMode, shootId, labId]);
  useEffect(() => () => useCreator.getState().close(), []);

  useEffect(() => {
    const before = document.title;
    document.title = lab ? `${lab.title} · Creative lab` : "Creative lab · ShootAgent";
    return () => {
      document.title = before;
    };
  }, [lab?.title]);

  // action errors (start, cancel, simulate) go to the app's toast
  useEffect(() => {
    if (!notice) return;
    useStore.getState().showToast(notice, "error");
    useCreator.getState().dismissNotice();
  }, [notice]);

  return (
    <div className="cl">
      <LabHeader />
      {lab && <StageBar />}
      {stale && (
        <p className="cl-stale" role="status">
          {stale}
        </p>
      )}
      <main className="cl-main">
        {error ? (
          <ErrorState message={error} />
        ) : lab ? (
          <>
            <RoomSection key={`room-${lab.id}`} />
            <ReelsSection key={`reels-${lab.id}`} />
            <ScorecardSection key={`score-${lab.id}`} />
            <LaunchSection key={`launch-${lab.id}`} />
          </>
        ) : loading || !labsLoaded ? (
          <div className="cl-loading" aria-busy="true">
            <span className="spinner" aria-hidden /> Opening the lab…
          </div>
        ) : (
          <EmptyState />
        )}
      </main>
      <ConceptDrawer />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  const apiMode = useStore((s) => s.apiMode);
  const setApiMode = useStore((s) => s.setApiMode);
  const retry = () => {
    const { api, shootId, labId } = useCreator.getState();
    if (api && shootId) {
      useCreator.getState().close();
      void useCreator.getState().open(api, shootId, labId);
    }
  };
  return (
    <div className="empty-state cl-error" role="alert">
      <h3>Couldn't open the creative lab</h3>
      <p>{message}</p>
      <div className="row-actions">
        <button className="btn" onClick={retry}>
          Retry
        </button>
        {apiMode === "live" && (
          <button className="btn btn-quiet" onClick={() => setApiMode("mock")}>
            Use the demo replay instead
          </button>
        )}
      </div>
    </div>
  );
}

const STEPS = [
  { icon: Lightbulb, title: "Writers' room", body: "Six creators with different lenses pitch reels; three critics score them for originality, truth and stop power; the room remixes and steals; the showrunner greenlights two." },
  { icon: Clapperboard, title: "Render", body: "The template reel, the two finalists and the single agent's best are rendered 9:16 with kinetic text, voice and animation cards." },
  { icon: Scale, title: "Independent panel", body: "Eleven viewer agents react as their audience segment, five judges rate blind, and novelty is measured against a cliche corpus." },
  { icon: TrendingUp, title: "Launch sim", body: "A transparent model turns the panel's reactions into 48 hours of views, comments and inquiries per reel." },
];

function EmptyState() {
  const start = useCreator((s) => s.start);
  const starting = useCreator((s) => s.starting);
  return (
    <section className="cl-empty">
      <p className="cl-kicker">Creative lab</p>
      <h2>Which reel would travel further?</h2>
      <p className="cl-empty-lede">
        A network of agents invents listing reels for this shoot, renders them, and has an independent panel measure, honestly, whether the work is creative and how far it would
        travel. The template reel and a single agent working alone run beside the room for comparison.
      </p>
      <ol className="cl-empty-steps">
        {STEPS.map((s, i) => (
          <li key={s.title}>
            <span className="cl-empty-n num">{String(i + 1).padStart(2, "0")}</span>
            <s.icon size={18} strokeWidth={1.6} aria-hidden />
            <h3>{s.title}</h3>
            <p>{s.body}</p>
          </li>
        ))}
      </ol>
      <button className="btn btn-agent cl-empty-go" onClick={() => void start({ ...DEFAULT_OPTIONS, creators: null })} disabled={starting}>
        <Play size={13} aria-hidden /> {starting ? "Starting…" : "Run creative lab"}
      </button>
      <p className="cl-empty-note">Takes about ten minutes. Options (finalists, rounds, voice) are under the arrow next to Run in the header.</p>
    </section>
  );
}
