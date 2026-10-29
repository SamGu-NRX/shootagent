import { Bell, BellOff, Check, Mail, Phone } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, passcode } from "../../api/client";
import type { Inbox, Lead, ListingCard } from "../../api/listing";
import { relTime } from "../../lib/format";
import { isSampleListing } from "../../listing/sample";
import { useListingApi } from "../../state/listingApi";
import { useStore } from "../../state/store";
import { Brand } from "../ShootList";

type Load = { state: "loading" } | { state: "ready"; inbox: Inbox } | { state: "locked" } | { state: "error"; message: string };

/** Realtor's phone: new tour requests with the buyer's questions, and film approval per listing. */
export function RealtorView({ shootId, leadId }: { shootId?: string; leadId?: string }) {
  const api = useListingApi();
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const refresh = useCallback(() => {
    api.inbox().then(
      (inbox) => setLoad({ state: "ready", inbox }),
      (e) => setLoad(e instanceof ApiError && e.status === 401 ? { state: "locked" } : { state: "error", message: String(e?.message ?? e) }),
    );
  }, [api]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000); // new requests also arrive as a push; this keeps an open page current
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="realtor">
      <header className="realtor-bar">
        <Brand />
        <span className="realtor-title">Leads</span>
        {load.state === "ready" && (
          <span className="notify-state" data-on={load.inbox.notifications || undefined}>
            {load.inbox.notifications ? <Bell size={14} aria-hidden /> : <BellOff size={14} aria-hidden />}
            {load.inbox.notifications ? "Push on" : "Push off"}
          </span>
        )}
      </header>
      <main className="realtor-main">
        <h1 className="sr-only" tabIndex={-1}>
          Leads
        </h1>
        {load.state === "loading" && <div className="pane-empty"><span className="spinner" aria-label="Loading leads" /></div>}
        {load.state === "locked" && <Unlock onUnlock={refresh} />}
        {load.state === "error" && (
          <div className="pane-empty" role="alert">
            <p>{load.message}</p>
            <button className="btn" onClick={refresh}>Retry</button>
          </div>
        )}
        {load.state === "ready" && <InboxView inbox={load.inbox} focusShoot={shootId} focusLead={leadId} onChange={refresh} />}
      </main>
    </div>
  );
}

function Unlock({ onUnlock }: { onUnlock: () => void }) {
  const [code, setCode] = useState("");
  return (
    <form
      className="empty-state unlock"
      onSubmit={(e) => {
        e.preventDefault();
        passcode.set(code.trim());
        onUnlock();
      }}
    >
      <h3>Enter the passcode to see your leads</h3>
      <label className="field">
        <span>Passcode</span>
        <input value={code} onChange={(e) => setCode(e.target.value)} autoFocus autoComplete="current-password" type="password" />
      </label>
      <button className="btn btn-primary" type="submit" disabled={!code.trim()}>
        Unlock
      </button>
    </form>
  );
}

function InboxView({ inbox, focusShoot, focusLead, onChange }: { inbox: Inbox; focusShoot?: string; focusLead?: string; onChange: () => void }) {
  const fresh = inbox.leads.filter((l) => l.status === "new");
  const seen = inbox.leads.filter((l) => l.status === "seen");
  return (
    <>
      {inbox.listings.map((c) => (
        <ListingReview key={c.shoot_id} card={c} onChange={onChange} />
      ))}
      <section aria-labelledby="new-title" className="lead-group">
        <h2 id="new-title" className="group-title">
          New <span className="num">{fresh.length}</span>
        </h2>
        {fresh.length === 0 && <p className="muted lead-empty">No new tour requests. They appear here, and on your phone, the moment a buyer books.</p>}
        <ul className="leads">
          {fresh.map((l) => (
            <LeadCard key={l.id} lead={l} focused={l.id === focusLead && l.shoot_id === (focusShoot ?? l.shoot_id)} onChange={onChange} />
          ))}
        </ul>
      </section>
      {seen.length > 0 && (
        <section aria-labelledby="seen-title" className="lead-group">
          <h2 id="seen-title" className="group-title">
            Handled <span className="num">{seen.length}</span>
          </h2>
          <ul className="leads">
            {seen.map((l) => (
              <LeadCard key={l.id} lead={l} focused={l.id === focusLead} onChange={onChange} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function ListingReview({ card, onChange }: { card: ListingCard; onChange: () => void }) {
  const api = useListingApi();
  const storeApi = useStore((s) => s.api);
  const [note, setNote] = useState(card.note);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const save = async (body: { film_approved?: boolean; note?: string }) => {
    setBusy(true);
    try {
      await api.review(card.shoot_id, body);
      onChange();
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="listing-card" aria-label={card.address ?? card.title}>
      <div className="listing-card-top">
        {card.thumb && <img className="listing-thumb" src={storeApi.blobUrl(card.shoot_id, card.thumb, 240)} alt="" width={72} height={52} loading="lazy" decoding="async" />}
        <div className="listing-card-text">
          <p className="listing-address">{card.address ?? card.title}</p>
          <p className="listing-meta">
            <span className="status-pill" data-live={card.published || undefined}>
              <span className="status-dot" aria-hidden />
              {card.published ? "Live" : "Unpublished"}
            </span>
            {isSampleListing(card) && <span className="sample-tag">Sample listing</span>}
            {card.film_languages.length > 0 && <span className="num">Film · {card.film_languages.join(" + ").toUpperCase()}</span>}
          </p>
        </div>
      </div>
      {card.film_languages.length > 0 && (
        <div className="review-actions">
          {card.film_approved ? (
            <p className="approved">
              <Check size={15} aria-hidden /> Film approved
              <button className="link" onClick={() => void save({ film_approved: false })} disabled={busy}>
                Undo
              </button>
            </p>
          ) : (
            <button className="btn btn-primary btn-lg" onClick={() => void save({ film_approved: true })} disabled={busy}>
              <Check size={15} aria-hidden /> Approve film
            </button>
          )}
          {!editing && (
            <button className="btn btn-lg" onClick={() => setEditing(true)}>
              {card.note ? "Edit note" : "Add a note"}
            </button>
          )}
        </div>
      )}
      {card.note && !editing && <p className="review-note-text">“{card.note}”</p>}
      {editing && (
        <form
          className="note-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save({ note });
          }}
        >
          <label className="field">
            <span>Note for the film</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={1000} autoFocus placeholder="Open on the kitchen, not the bedroom." />
          </label>
          <div className="row-actions">
            <button type="button" className="btn btn-quiet" onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Save note
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function LeadCard({ lead, focused, onChange }: { lead: Lead; focused: boolean; onChange: () => void }) {
  const api = useListingApi();
  const ref = useRef<HTMLLIElement>(null);
  const isEmail = lead.contact.includes("@");
  const phone = lead.contact.replace(/[^\d+]/g, "");
  const open = lead.questions.filter((q) => q.status === "follow_up").length;

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [focused]);

  return (
    <li ref={ref} className="lead" data-new={lead.status === "new" || undefined} data-focused={focused || undefined}>
      <div className="lead-head">
        <h3 className="lead-name">{lead.name}</h3>
        <span className="lead-time">{relTime(lead.created_at)}</span>
      </div>
      <p className="lead-when">
        <span className="lead-key">Wants to see it</span> {lead.preferred_time}
      </p>
      <p className="lead-where muted">{lead.address}</p>
      <div className="lead-contact">
        <a className="btn btn-lg" href={isEmail ? `mailto:${lead.contact}` : `tel:${phone}`}>
          {isEmail ? <Mail size={15} aria-hidden /> : <Phone size={15} aria-hidden />}
          {lead.contact}
        </a>
      </div>
      {lead.questions.length > 0 && (
        <div className="lead-qs">
          <p className="lead-key">
            Asked on the listing page{open > 0 && <span className="needs"> · {open} need{open === 1 ? "s" : ""} your answer</span>}
          </p>
          <ul>
            {lead.questions.map((q, i) => (
              <li key={i} data-status={q.status}>
                <p className="q">“{q.question}”</p>
                {q.status === "follow_up" && <p className="a-tag">Needs your reply</p>}
                {q.answer && <p className="a">{q.answer}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="lead-foot">
        <span className="muted small">
          {lead.notified.sent ? "Pushed to your phone" : lead.notified.error ? `Push failed: ${lead.notified.error}` : ""}
        </span>
        {lead.status === "new" && (
          <button
            className="btn"
            onClick={async () => {
              await api.markSeen(lead.shoot_id, lead.id);
              onChange();
            }}
          >
            <Check size={14} aria-hidden /> Mark handled
          </button>
        )}
      </div>
    </li>
  );
}
