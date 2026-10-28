import { ArrowUp, Check, ChevronLeft, ChevronRight, Eye, MapPin, MessageSquare, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { CommentView, ReviewShoot, ReviewView } from "../api/types";
import { message, useStore } from "../state/store";

const ACTIVE = new Set(["started", "queued", "working"]);

const STATE: Record<string, { text: string; tone: string }> = {
  sent: { text: "Sent to your photographer", tone: "quiet" },
  declined: { text: "Can't be done", tone: "bad" },
  started: { text: "Queued", tone: "busy" },
  queued: { text: "Queued", tone: "busy" },
  working: { text: "Editing now", tone: "busy" },
  applied: { text: "Done", tone: "good" },
  done: { text: "Done", tone: "good" },
  not_needed: { text: "Nothing to change", tone: "quiet" },
  failed: { text: "Couldn't do it automatically", tone: "bad" },
  undone: { text: "Undone", tone: "quiet" },
  stopped: { text: "Stopped", tone: "bad" },
};

/** The client's side of a review link: approve photos, or point at something and ask for a change. */
export function ReviewPage({ token }: { token: string }) {
  const api = useStore((s) => s.api);
  const [data, setData] = useState<ReviewShoot | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.getReview(token));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", message(e)));
    }
  }, [api, token]);

  useEffect(() => {
    void load();
  }, [load]);

  // While the assistant is working on a request, keep the page current.
  const busy = data?.comments.some((c) => ACTIVE.has(c.state)) ?? false;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => void load(), 1500);
    return () => clearInterval(t);
  }, [busy, load]);

  const views = data?.views ?? [];
  const approved = views.filter((v) => v.client?.status === "approved").length;
  const idx = views.findIndex((v) => v.id === open);

  return (
    <div className="client-app rv">
      <header className="pub-bar">
        <span className="pub-brand">
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden>
            <path d="M2 7V2h5M18 7V2h-5M2 13v5h5M18 13v5h-5" stroke="currentColor" strokeWidth="1.8" fill="none" />
            <circle cx="10" cy="10" r="2.4" fill="var(--agent)" />
          </svg>
          Photo review
        </span>
        {data && (
          <span className="rv-progress num" aria-label={`${approved} of ${views.length} photos approved`}>
            <span className="rv-bar">
              <span style={{ width: `${views.length ? (approved / views.length) * 100 : 0}%` }} />
            </span>
            {approved}/{views.length} approved
          </span>
        )}
      </header>
      <main className="rv-main">
        {error && (
          <div className="pub-empty" role="alert">
            <h1>This review link isn't working</h1>
            <p>{error.message}</p>
          </div>
        )}
        {!data && !error && (
          <div className="pub-empty">
            <span className="spinner" aria-label="Loading" />
          </div>
        )}
        {data && (
          <>
            <section className="pub-intro">
              <h1>{data.title}</h1>
              <p className="pub-lede">
                Tap a photo to look closer. Approve it, or point at what needs to change and say what to do. The editing
                assistant makes the change, checks it, and shows you the result; changes that would misrepresent the property
                (hiding damage, removing power lines) are declined.
              </p>
              {!data.agent_available && (
                <p className="rv-note">The editing assistant is offline right now; your requests go straight to the photographer.</p>
              )}
            </section>
            <ul className="rv-grid">
              {views.map((v) => {
                const comments = data.comments.filter((c) => c.view === v.id);
                const working = comments.some((c) => ACTIVE.has(c.state));
                return (
                  <li key={v.id}>
                    <button className="rv-card" onClick={() => setOpen(v.id)} data-verdict={v.client?.status}>
                      <span className="rv-thumb">
                        <img src={api.reviewBlobUrl(token, v.blob, 640)} alt="" loading="lazy" />
                        {v.client?.status === "approved" && (
                          <span className="rv-ok" aria-label="Approved">
                            <Check size={13} strokeWidth={2.6} />
                          </span>
                        )}
                      </span>
                      <span className="rv-card-label">{v.label}</span>
                      <span className="rv-card-meta">
                        {working ? (
                          <span data-tone="busy">Editing…</span>
                        ) : v.client?.status === "changes_requested" ? (
                          <span data-tone="bad">Changes requested</span>
                        ) : v.client?.status === "approved" ? (
                          <span data-tone="good">Approved</span>
                        ) : (
                          <span>Waiting for you</span>
                        )}
                        {comments.length > 0 && (
                          <span className="rv-count">
                            <MessageSquare size={11} aria-hidden /> {comments.length}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {data.public_token && (
              <p className="rv-foot">
                Buyers see each photo's disclosure and original on the{" "}
                <a className="link" href={`#/d/${encodeURIComponent(data.public_token)}`} target="_blank" rel="noreferrer">
                  public disclosure page
                </a>
                .
              </p>
            )}
          </>
        )}
      </main>
      {data && idx >= 0 && (
        <PhotoSheet
          key={open}
          token={token}
          data={data}
          view={views[idx]!}
          onClose={() => setOpen(null)}
          onStep={(d) => setOpen(views[(idx + d + views.length) % views.length]!.id)}
          onChange={setData}
          reload={load}
        />
      )}
    </div>
  );
}

function PhotoSheet({
  token,
  data,
  view,
  onClose,
  onStep,
  onChange,
  reload,
}: {
  token: string;
  data: ReviewShoot;
  view: ReviewView;
  onClose: () => void;
  onStep: (d: number) => void;
  onChange: (d: ReviewShoot) => void;
  reload: () => Promise<void>;
}) {
  const api = useStore((s) => s.api);
  const [point, setPoint] = useState<[number, number] | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const img = useRef<HTMLImageElement>(null);
  const comments = data.comments.filter((c) => c.view === view.id).slice().reverse();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "TEXTAREA") return;
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") onStep(-1);
      else if (e.key === "ArrowRight") onStep(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onStep]);

  const verdict = async (status: "approved" | "changes_requested" | null) => {
    try {
      onChange(await api.reviewVerdict(token, view.id, status));
    } catch (e) {
      setErr(message(e));
    }
  };

  const send = async () => {
    if (!text.trim() || sending) return;
    setSending(true);
    setErr(null);
    try {
      await api.reviewComment(token, { view: view.id, text: text.trim(), ...(point ? { point } : {}) });
      setText("");
      setPoint(null);
      if (view.client?.status !== "changes_requested") await api.reviewVerdict(token, view.id, "changes_requested");
      await reload();
    } catch (e) {
      setErr(message(e));
    } finally {
      setSending(false);
    }
  };

  const undo = async (c: CommentView) => {
    try {
      await api.reviewUndo(token, c.id);
      await reload();
    } catch (e) {
      setErr(message(e));
    }
  };

  return (
    <div className="rv-sheet" role="dialog" aria-modal="true" aria-label={view.label}>
      <div className="rv-sheet-inner">
        <div className="rv-stage">
          <div
            className="rv-photo"
            onClick={(e) => {
              const r = img.current?.getBoundingClientRect();
              if (!r) return;
              const x = (e.clientX - r.left) / r.width;
              const y = (e.clientY - r.top) / r.height;
              if (x >= 0 && x <= 1 && y >= 0 && y <= 1) setPoint([+x.toFixed(4), +y.toFixed(4)]);
            }}
          >
            <img
              ref={img}
              src={api.reviewBlobUrl(token, showOriginal ? view.original_blob : view.blob, 1600)}
              alt={`${view.label}${showOriginal ? ", before edits" : ""}`}
              draggable={false}
            />
            {point && (
              <span className="rv-pin" style={{ left: `${point[0] * 100}%`, top: `${point[1] * 100}%` }} aria-hidden>
                <MapPin size={22} strokeWidth={2.2} />
              </span>
            )}
          </div>
          <div className="rv-stage-bar">
            <button className="rv-nav" onClick={() => onStep(-1)} aria-label="Previous photo">
              <ChevronLeft size={18} />
            </button>
            <span className="rv-stage-title">{view.label}</span>
            {view.edited && (
              <button
                className="rv-hold"
                onPointerDown={() => setShowOriginal(true)}
                onPointerUp={() => setShowOriginal(false)}
                onPointerLeave={() => setShowOriginal(false)}
                onKeyDown={(e) => e.key === " " && setShowOriginal(true)}
                onKeyUp={() => setShowOriginal(false)}
              >
                <Eye size={14} aria-hidden /> Hold for before
              </button>
            )}
            <button className="rv-nav" onClick={() => onStep(1)} aria-label="Next photo">
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <aside className="rv-side">
          <div className="rv-side-head">
            <h2>{view.label}</h2>
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              <X size={18} />
            </button>
          </div>
          {view.disclosure && <p className="rv-disclosure">On the listing this photo is marked: “{view.disclosure}”</p>}
          <div className="rv-verdict" role="group" aria-label="Your verdict">
            <button
              className={view.client?.status === "approved" ? "btn btn-approve-on" : "btn"}
              onClick={() => void verdict(view.client?.status === "approved" ? null : "approved")}
            >
              <Check size={14} aria-hidden /> {view.client?.status === "approved" ? "Approved" : "Approve"}
            </button>
            <button
              className={view.client?.status === "changes_requested" ? "btn btn-changes-on" : "btn"}
              onClick={() => void verdict(view.client?.status === "changes_requested" ? null : "changes_requested")}
            >
              Needs changes
            </button>
          </div>
          <form
            className="rv-compose"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <label htmlFor="rv-text">Ask for a change</label>
            <textarea
              id="rv-text"
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Remove the car from the driveway"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="rv-compose-row">
              <span className="rv-hint">
                {point ? (
                  <>
                    <MapPin size={12} aria-hidden /> Pointing at the spot you tapped ·{" "}
                    <button type="button" className="link" onClick={() => setPoint(null)}>
                      clear
                    </button>
                  </>
                ) : (
                  "Tap the photo to point at something (optional)."
                )}
              </span>
              <button type="submit" className="send" disabled={!text.trim() || sending} aria-label="Send request">
                <ArrowUp size={15} strokeWidth={2.2} />
              </button>
            </div>
            {err && (
              <p className="inline-error" role="alert">
                {err}
              </p>
            )}
            <p className="rv-left num">{data.requests_left} automatic edits left on this link</p>
          </form>
          {comments.length > 0 && (
            <ol className="rv-thread" aria-label="Your requests on this photo">
              {comments.map((c) => {
                const st = STATE[c.state] ?? { text: c.state, tone: "quiet" };
                const canUndo = c.state === "applied" && c.result_version && c.current === c.result_version;
                return (
                  <li key={c.id}>
                    <p className="rv-msg">“{c.text}”</p>
                    <p className="rv-state" data-tone={st.tone}>
                      {ACTIVE.has(c.state) && <span className="spinner spinner-sm" aria-hidden />}
                      {st.text}
                      {ACTIVE.has(c.state) && c.steps > 0 ? ` · ${c.steps} steps` : ""}
                    </p>
                    {(c.reason || c.message) && !ACTIVE.has(c.state) && (
                      <p className="rv-reason">{c.state === "applied" ? c.message || c.reason : c.reason || c.message}</p>
                    )}
                    {canUndo && (
                      <button className="link" onClick={() => void undo(c)}>
                        <RotateCcw size={12} aria-hidden /> Undo this change
                      </button>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </aside>
      </div>
    </div>
  );
}
