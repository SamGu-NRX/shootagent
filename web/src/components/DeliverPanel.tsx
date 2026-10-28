import { Check, ChevronRight, Copy, Download, ExternalLink, FileArchive, Link2, Link2Off, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Change, CommentView, ComplianceReport, KitInfo, LintResult, Listing, PhotoReport } from "../api/types";
import { relTime } from "../lib/format";
import { message, useStore } from "../state/store";

/** Absolute link to a hash route on this app, keeping ?api=mock so demo links open in demo mode. */
export function appLink(route: string): string {
  return `${location.origin}${location.pathname}${location.search}#${route}`;
}

const STATUS_TEXT: Record<string, string> = {
  altered: "Altered",
  unaltered: "Not altered",
  unverified: "Not checked",
  review: "Needs a decision",
  superseded: "Replaced",
};

const KIND_TEXT: Record<Change["kind"], string> = {
  alteration: "Alteration",
  adjustment: "Adjustment",
  restoration: "Restored",
  overlay: "Overlay",
  possible_alteration: "Unresolved",
};

function byWhom(c: Change): string {
  if (c.by === "editor") return c.requested_by === "order" ? "the editor" : "the editor (found by QC)";
  return c.requested_by === "client" ? "ShootAgent, for the client" : c.requested_by === "customer" ? "ShootAgent, on request" : c.requested_by === "qc" ? "ShootAgent QC" : "ShootAgent";
}

function quietSummary(p: PhotoReport): string {
  if (p.status === "unverified") return "QC has not compared this photo with its brackets yet.";
  const restored = p.changes.filter((c) => c.kind === "restoration").length;
  const adjusted = p.changes.filter((c) => c.kind === "adjustment" || c.kind === "overlay").length;
  if (restored) return `Repaired to match the camera${adjusted ? ", with tone adjustments" : ""}. No disclosure needed.`;
  if (adjusted) return "Tone and colour adjustments only. No disclosure needed.";
  return "As captured. No disclosure needed.";
}

function CopyButton({ text, label = "Copy link" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-sm"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1400);
        });
      }}
    >
      {done ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />} {done ? "Copied" : label}
    </button>
  );
}

/** Demo data lives in this tab's memory, so its links open here; live links open a new tab. */
function useLinkTarget(): { target?: string; rel?: string } {
  const mode = useStore((s) => s.apiMode);
  return mode === "mock" ? {} : { target: "_blank", rel: "noreferrer" };
}

export function DeliverPanel() {
  const linkTarget = useLinkTarget();
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  const select = useStore((s) => s.selectView);
  const refreshShoot = useStore((s) => s.refreshShoot);
  const showToast = useStore((s) => s.showToast);
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [comments, setComments] = useState<CommentView[]>([]);
  const [kit, setKit] = useState<KitInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const shootId = shoot?.id;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reload = useCallback(async () => {
    if (!shootId) return;
    try {
      const [r, c, k] = await Promise.all([api.getCompliance(shootId), api.listComments(shootId), api.getKit(shootId)]);
      setReport(r);
      setComments(c);
      setKit(k);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  }, [api, shootId]);

  // The shoot object is replaced whenever a run changes it; follow it, a little debounced.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void reload(), report ? 300 : 0);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shoot, reload]);

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      showToast(message(e), "error");
    } finally {
      setBusy(null);
    }
  };

  if (!shoot) return null;
  if (!report) {
    return (
      <div className="dv">
        {error ? (
          <div className="panel-empty" role="alert">
            <h2>Couldn't load the disclosures</h2>
            <p>{error}</p>
            <button className="btn" onClick={() => void reload()}>
              Retry
            </button>
          </div>
        ) : (
          <div className="dv-loading">
            <span className="spinner" aria-label="Loading" />
          </div>
        )}
      </div>
    );
  }

  const byView = new Map(report.photos.map((p) => [p.view, p]));
  const listing = report.listing.map((v) => byView.get(v)!).filter(Boolean);
  const out = report.photos.filter((p) => !report.listing.includes(p.view));
  const altered = listing.filter((p) => p.status === "altered").length;
  const repaired = listing.filter((p) => p.status === "unaltered" && p.changes.some((c) => c.kind === "restoration")).length;
  const pub = report.publication;
  const share = shoot.shares?.filter((s) => !s.revoked).slice(-1)[0] ?? null;

  return (
    <div className="dv">
      <section className="dv-sec" aria-labelledby="dv-disclosures">
        <header className="dv-head">
          <h2 id="dv-disclosures">Disclosures</h2>
          <span className="dv-law" title="California Business and Professions Code 10140.8, in force since 1 January 2026">
            AB 723
          </span>
        </header>
        <p className="dv-lede">
          {altered === 0 ? "None" : `${altered} of ${listing.length}`} of the listing photos {altered === 1 ? "is" : "are"} digitally altered
          {repaired ? `; ${repaired} ${repaired === 1 ? "was" : "were"} repaired to match the camera and need${repaired === 1 ? "s" : ""} no disclosure` : ""}.
          Altered photos are labelled and paired with their unaltered original.
        </p>
        {report.issues.length > 0 && (
          <ul className="dv-issues">
            {report.issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        )}
        <ol className="dv-photos">
          {listing.map((p, i) => (
            <PhotoRow
              key={p.view}
              p={p}
              n={i + 1}
              onOpen={() => select(p.view)}
              onVerdict={(finding, verdict) =>
                act("verdict", async () => {
                  await api.setFindingVerdict(shoot.id, p.view, finding, verdict);
                  await refreshShoot();
                  await reload();
                })
              }
              busy={busy === "verdict"}
            />
          ))}
        </ol>
        {out.length > 0 && (
          <details className="dv-out">
            <summary>
              <ChevronRight size={13} aria-hidden /> Not in the listing ({out.length})
            </summary>
            <ul>
              {out.map((p) => (
                <li key={p.view}>
                  <span>{p.label}</span>
                  <span className="muted">{p.status === "superseded" ? "replaced by its split views" : "not approved"}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="dv-publish">
          <div className="dv-publish-state">
            {pub ? (
              <>
                <span className={report.stale ? "dv-dot dv-dot-stale" : "dv-dot"} aria-hidden />
                <span>
                  Published v{pub.version}
                  <span className="dv-sep" aria-hidden>·</span>
                  {relTime(pub.created_at)}
                  {report.stale && (
                    <>
                      <span className="dv-sep" aria-hidden>·</span>photos changed since
                    </>
                  )}
                </span>
              </>
            ) : (
              <span className="muted">Not published yet</span>
            )}
          </div>
          <div className="dv-actions">
            {pub && (
              <a className="btn btn-sm" href={`#/d/${encodeURIComponent(pub.token)}`} {...linkTarget}>
                <ExternalLink size={13} aria-hidden /> Public page
              </a>
            )}
            {pub && <CopyButton text={appLink(`/d/${encodeURIComponent(pub.token)}`)} />}
            <button
              className={!pub || report.stale ? "btn btn-sm btn-primary" : "btn btn-sm"}
              disabled={busy === "publish" || !listing.length}
              onClick={() =>
                act("publish", async () => {
                  await api.publish(shoot.id);
                  await refreshShoot();
                  await reload();
                  showToast("Published: disclosures, originals and edit records are live.");
                })
              }
            >
              <ShieldCheck size={13} aria-hidden /> {busy === "publish" ? "Publishing…" : pub ? (report.stale ? "Publish again" : "Republish") : "Publish"}
            </button>
          </div>
        </div>
      </section>

      <ReviewSection
        share={share}
        comments={comments}
        listingCount={listing.length}
        clientApproved={shoot.views.filter((v) => v.client?.status === "approved" && report.listing.includes(v.id)).length}
        busy={busy}
        onCreate={() =>
          act("share", async () => {
            await api.createShare(shoot.id, "Client");
            await refreshShoot();
          })
        }
        onRevoke={(token) =>
          act("share", async () => {
            await api.revokeShare(shoot.id, token);
            await refreshShoot();
          })
        }
        onOpenView={select}
      />

      <KitSection
        listing={shoot.listing}
        kit={kit}
        stale={report.stale}
        busy={busy}
        onSave={(fields) =>
          act("listing", async () => {
            await api.patchListing(shoot.id, fields);
            await refreshShoot();
            showToast("Listing details saved.");
          })
        }
        onBuild={() =>
          act("kit", async () => {
            setKit(await api.buildKit(shoot.id));
            await refreshShoot();
            await reload();
          })
        }
      />
    </div>
  );
}

function PhotoRow({
  p,
  n,
  onOpen,
  onVerdict,
  busy,
}: {
  p: PhotoReport;
  n: number;
  onOpen: () => void;
  onVerdict: (finding: string, verdict: "disclose" | "not_alteration") => void;
  busy: boolean;
}) {
  const shoot = useStore((s) => s.shoot);
  const api = useStore((s) => s.api);
  const pending = p.changes.filter((c) => c.kind === "possible_alteration" && c.finding);
  const shown = p.changes.filter((c) => c.kind !== "possible_alteration");
  return (
    <li className="dv-photo" data-status={p.status}>
      <button className="dv-thumb" onClick={onOpen} title={`Show ${p.label} in the viewer`}>
        {p.final_blob && shoot && <img src={api.blobUrl(shoot.id, p.final_blob, 160)} alt="" loading="lazy" />}
      </button>
      <div className="dv-photo-body">
        <div className="dv-photo-top">
          <span className="dv-photo-name">
            <span className="num muted">{n}</span> {p.label}
          </span>
          <span className="dv-chip" data-status={p.status}>
            {STATUS_TEXT[p.status] ?? p.status}
          </span>
        </div>
        {p.disclosure ? <p className="dv-disclosure">“{p.disclosure}”</p> : <p className="dv-quiet">{quietSummary(p)}</p>}
        {pending.map((c) => (
          <div key={c.finding} className="dv-decide">
            <p>
              <span className="dv-decide-q">QC can't tell whether the editor changed this:</span> {c.what}
            </p>
            <div className="dv-actions">
              <button className="btn btn-sm" disabled={busy} onClick={() => onVerdict(c.finding!, "disclose")}>
                Disclose as altered
              </button>
              <button className="btn btn-sm btn-quiet" disabled={busy} onClick={() => onVerdict(c.finding!, "not_alteration")}>
                Not an alteration
              </button>
            </div>
          </div>
        ))}
        {shown.length > 0 && (
          <details className="dv-changes">
            <summary>
              <ChevronRight size={12} aria-hidden /> {shown.length} change{shown.length > 1 ? "s" : ""} on record
            </summary>
            <ul>
              {shown.map((c, i) => (
                <li key={i} data-kind={c.kind}>
                  <span className="dv-kind">{KIND_TEXT[c.kind]}</span>
                  <span>
                    {c.what} <span className="muted">· {byWhom(c)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </li>
  );
}

const STATE_TEXT: Record<string, string> = {
  sent: "With the photographer",
  declined: "Declined",
  started: "Queued",
  queued: "Queued",
  working: "Working",
  applied: "Done",
  not_needed: "Not needed",
  failed: "Couldn't do",
  undone: "Undone",
  stopped: "Stopped",
  done: "Done",
};

function ReviewSection({
  share,
  comments,
  listingCount,
  clientApproved,
  busy,
  onCreate,
  onRevoke,
  onOpenView,
}: {
  share: { token: string; created_at: string } | null;
  comments: CommentView[];
  listingCount: number;
  clientApproved: number;
  busy: string | null;
  onCreate: () => void;
  onRevoke: (token: string) => void;
  onOpenView: (view: string) => void;
}) {
  const shoot = useStore((s) => s.shoot);
  const linkTarget = useLinkTarget();
  const url = share ? appLink(`/r/${encodeURIComponent(share.token)}`) : "";
  const recent = [...comments].reverse().slice(0, 6);
  const label = (view: string) => shoot?.views.find((v) => v.id === view)?.label || view;
  return (
    <section className="dv-sec" aria-labelledby="dv-review">
      <header className="dv-head">
        <h2 id="dv-review">Client review</h2>
        {share && (
          <span className="dv-count num">
            {clientApproved}/{listingCount} approved
          </span>
        )}
      </header>
      {!share ? (
        <>
          <p className="dv-lede">
            Send the listing agent a link. They approve photos or tap a spot and ask for a change; ShootAgent makes it, checks it,
            and applies the same listing photo policy as in chat.
          </p>
          <button className="btn btn-sm btn-primary" onClick={onCreate} disabled={busy === "share"}>
            <Link2 size={13} aria-hidden /> Create review link
          </button>
        </>
      ) : (
        <>
          <div className="dv-link">
            <input readOnly value={url} aria-label="Client review link" onFocus={(e) => e.currentTarget.select()} />
          </div>
          <div className="dv-actions">
            <CopyButton text={url} />
            <a className="btn btn-sm" href={`#/r/${encodeURIComponent(share.token)}`} {...linkTarget}>
              <ExternalLink size={13} aria-hidden /> Open as client
            </a>
            <button className="btn btn-sm btn-quiet" onClick={() => onRevoke(share.token)} disabled={busy === "share"}>
              <Link2Off size={13} aria-hidden /> Turn off
            </button>
          </div>
          {recent.length > 0 ? (
            <ul className="dv-comments" aria-label="Client requests">
              {recent.map((c) => (
                <li key={c.id}>
                  <button className="dv-comment" onClick={() => onOpenView(c.view)}>
                    <span className="dv-comment-text">“{c.text}”</span>
                    <span className="dv-comment-meta">
                      {label(c.view)} · <span data-state={c.state}>{STATE_TEXT[c.state] ?? c.state}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="dv-quiet">No requests from the client yet.</p>
          )}
        </>
      )}
    </section>
  );
}

const KIT_GROUPS: [string, string, string][] = [
  ["photos/", "MLS photo", "MLS photos"],
  ["photos-labeled/", "labelled copy", "labelled copies"],
  ["originals/", "unaltered original", "unaltered originals"],
];

function KitSection({
  listing,
  kit,
  stale,
  busy,
  onSave,
  onBuild,
}: {
  listing: Listing;
  kit: KitInfo | null;
  stale: boolean;
  busy: string | null;
  onSave: (fields: Partial<Listing>) => void;
  onBuild: () => void;
}) {
  const api = useStore((s) => s.api);
  const [form, setForm] = useState(() => toForm(listing));
  const [lint, setLint] = useState<LintResult | null>(null);
  const dirty = JSON.stringify(form) !== JSON.stringify(toForm(listing));

  useEffect(() => setForm(toForm(listing)), [listing]);
  const copy = `${form.headline} ${form.description}`.trim();
  useEffect(() => {
    if (!copy) {
      setLint(null);
      return;
    }
    const t = setTimeout(() => void api.lintListing(copy).then(setLint).catch(() => setLint(null)), 350);
    return () => clearTimeout(t);
  }, [api, copy]);

  const groups = useMemo(() => {
    if (!kit) return [];
    return KIT_GROUPS.map(([prefix, one, many]) => {
      const n = kit.files.filter((f) => f.path.startsWith(prefix)).length;
      return { n, name: n === 1 ? one : many };
    }).filter((g) => g.n);
  }, [kit]);

  const set = (k: keyof ReturnType<typeof toForm>) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <section className="dv-sec" aria-labelledby="dv-kit">
      <header className="dv-head">
        <h2 id="dv-kit">Listing kit</h2>
      </header>
      <form
        className="dv-form"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(fromForm(form));
        }}
      >
        <label className="dv-field dv-wide">
          <span>Address</span>
          <input value={form.address} onChange={set("address")} placeholder="412 Maple Court, Austin, TX" />
        </label>
        <label className="dv-field">
          <span>Price</span>
          <input value={form.price} onChange={set("price")} placeholder="$649,000" />
        </label>
        <label className="dv-field dv-small">
          <span>Beds</span>
          <input value={form.beds} onChange={set("beds")} inputMode="decimal" />
        </label>
        <label className="dv-field dv-small">
          <span>Baths</span>
          <input value={form.baths} onChange={set("baths")} inputMode="decimal" />
        </label>
        <label className="dv-field dv-small">
          <span>Sq ft</span>
          <input value={form.sqft} onChange={set("sqft")} inputMode="numeric" />
        </label>
        <label className="dv-field dv-wide">
          <span>Headline</span>
          <input value={form.headline} onChange={set("headline")} placeholder="Light-filled craftsman on a quiet street" />
        </label>
        <label className="dv-field dv-wide">
          <span>Remarks</span>
          <textarea value={form.description} onChange={set("description")} rows={3} placeholder="Describe the home: rooms, light, materials, updates." />
        </label>
        {lint && lint.flags.length > 0 && (
          <ul className="dv-lint dv-wide" aria-label="Fair-housing wording check">
            {lint.flags.map((f, i) => (
              <li key={i} data-severity={f.severity}>
                <b>“{f.phrase}”</b> {f.why} <span className="muted">Try: {f.suggestion}</span>
              </li>
            ))}
          </ul>
        )}
        {lint && lint.flags.length === 0 && copy && <p className="dv-lint-ok dv-wide">Fair-housing wording check: nothing flagged.</p>}
        <div className="dv-actions dv-wide">
          <button className="btn btn-sm" type="submit" disabled={!dirty || busy === "listing"}>
            {busy === "listing" ? "Saving…" : "Save details"}
          </button>
        </div>
      </form>
      <div className="dv-kit">
        <p className="dv-lede">
          One zip for the listing: MLS-ready photos in showing order, labelled copies for social posts and print, the unaltered
          originals, captions with alt text, the disclosures, a verifiable edit record and the reel.
        </p>
        {kit && (
          <div className="dv-kit-built">
            <FileArchive size={16} aria-hidden />
            <div>
              <p className="dv-kit-name">{kit.name}</p>
              <p className="muted num">
                {(kit.size / 1e6).toFixed(1)} MB · {kit.photos} photos · {kit.altered} altered · built {relTime(kit.built_at)}
              </p>
              <p className="dv-kit-groups">
                {groups.map((g) => `${g.n} ${g.name}`).join(" · ")}
              </p>
            </div>
          </div>
        )}
        <div className="dv-actions">
          <button className={kit ? "btn btn-sm" : "btn btn-sm btn-primary"} onClick={onBuild} disabled={busy === "kit"}>
            <FileArchive size={13} aria-hidden /> {busy === "kit" ? "Building…" : kit ? "Rebuild kit" : "Build listing kit"}
          </button>
          {kit && !kit.demo && (
            <a className="btn btn-sm btn-primary" href={kit.url} download>
              <Download size={13} aria-hidden /> Download
            </a>
          )}
        </div>
        {kit?.demo && <p className="dv-quiet">Demo data builds the contents list only; the live server writes the zip.</p>}
        {stale && kit && <p className="dv-quiet">Photos changed since this kit was built. Rebuilding publishes them first.</p>}
      </div>
    </section>
  );
}

function toForm(l: Listing) {
  return {
    address: l.address ?? "",
    price: l.price ?? "",
    beds: l.beds != null ? String(l.beds) : "",
    baths: l.baths != null ? String(l.baths) : "",
    sqft: l.sqft != null ? String(l.sqft) : "",
    headline: l.headline ?? "",
    description: l.description ?? "",
  };
}

function fromForm(f: ReturnType<typeof toForm>): Partial<Listing> {
  const num = (v: string) => (v.trim() && Number.isFinite(Number(v)) ? Number(v) : null);
  return {
    address: f.address.trim() || null,
    price: f.price.trim() || null,
    beds: num(f.beds),
    baths: num(f.baths),
    sqft: num(f.sqft) != null ? Math.round(num(f.sqft)!) : null,
    headline: f.headline.trim() || null,
    description: f.description.trim() || null,
  };
}
