import { BadgeCheck, CircleAlert, FileJson, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import type { PublicPage, PublicPhoto } from "../api/types";
import { message, useStore } from "../state/store";
import { verifyPhoto, type VerifyResult } from "../lib/verify";
import { BeforeAfter } from "./BeforeAfter";

const BADGE: Record<string, { text: string; tone: string }> = {
  altered: { text: "Digitally altered", tone: "altered" },
  review: { text: "Digitally altered", tone: "altered" },
  unaltered: { text: "Not altered", tone: "clean" },
  unverified: { text: "Not altered by ShootAgent", tone: "quiet" },
};

function staged(p: PublicPhoto) {
  return p.disclosure?.startsWith("Virtually staged");
}

/** The read-only page a QR code or listing link opens: every photo, what changed, and the unaltered original. */
export function DisclosurePage({ token, focus }: { token: string; focus: string | null }) {
  const api = useStore((s) => s.api);
  const [page, setPage] = useState<PublicPage | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [results, setResults] = useState<Record<string, VerifyResult | "running">>({});

  useEffect(() => {
    let live = true;
    api
      .getPublic(token)
      .then((p) => live && setPage(p))
      .catch((e) => live && setError(e instanceof ApiError ? e : new ApiError(0, "error", message(e))));
    return () => {
      live = false;
    };
  }, [api, token]);

  useEffect(() => {
    if (page && focus) document.getElementById(`photo-${focus}`)?.scrollIntoView({ block: "start" });
  }, [page, focus]);

  const altered = useMemo(() => page?.photos.filter((p) => p.disclosure).length ?? 0, [page]);
  const file = (blob: string, w?: number) => api.publicBlobUrl(token, blob, w);

  const verifyAll = async () => {
    if (!page) return;
    for (const p of page.photos) {
      setResults((r) => ({ ...r, [p.view]: "running" }));
      const res = await verifyPhoto(p, (b) => file(b));
      setResults((r) => ({ ...r, [p.view]: res }));
    }
  };
  const done = page ? page.photos.map((p) => results[p.view]).filter((r): r is VerifyResult => !!r && r !== "running") : [];
  const allDone = page && done.length === page.photos.length;
  const allOk = allDone && done.every((r) => r.ok);

  return (
    <div className="client-app pub">
      <header className="pub-bar">
        <span className="pub-brand">
          <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden>
            <path d="M2 7V2h5M18 7V2h-5M2 13v5h5M18 13v5h-5" stroke="currentColor" strokeWidth="1.8" fill="none" />
            <circle cx="10" cy="10" r="2.4" fill="var(--agent)" />
          </svg>
          Photo disclosures
        </span>
        {page && <span className="pub-ver num">Published v{page.version}</span>}
      </header>
      <main className="pub-main">
        {error && (
          <div className="pub-empty" role="alert">
            <h1>This listing's photo disclosures aren't available</h1>
            <p>{error.message}</p>
          </div>
        )}
        {!page && !error && (
          <div className="pub-empty">
            <span className="spinner" aria-label="Loading" />
          </div>
        )}
        {page && (
          <>
            <section className="pub-intro">
              <h1>{page.title}</h1>
              <p className="pub-lede">
                {altered === 0
                  ? "None of this listing's photos are digitally altered."
                  : `${altered} of ${page.photos.length} photos in this listing ${altered === 1 ? "is" : "are"} digitally altered.`}{" "}
                California law (AB 723) requires altered listing photos to say so and to be shown with the unaltered original.
                Every photo below lists what changed; drag across an altered photo to compare it with the original.
              </p>
              <div className="pub-verify">
                <button className="btn btn-primary" onClick={() => void verifyAll()} disabled={Object.values(results).includes("running")}>
                  <ShieldCheck size={14} aria-hidden /> {Object.values(results).includes("running") ? "Checking…" : "Verify these records"}
                </button>
                <button
                  className="btn"
                  onClick={() => {
                    const blob = new Blob([JSON.stringify(page, null, 1)], { type: "application/json" });
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(blob);
                    a.download = "edit-record.json";
                    a.click();
                    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
                  }}
                >
                  <FileJson size={14} aria-hidden /> Download the edit record
                </button>
              </div>
              {allDone && (
                <p className="pub-verified" data-ok={allOk || undefined} role="status">
                  {allOk ? <BadgeCheck size={15} aria-hidden /> : <CircleAlert size={15} aria-hidden />}
                  {allOk
                    ? `Verified in your browser: ${done.reduce((n, r) => n + r.entries, 0)} records and ${done.reduce((n, r) => n + r.images, 0)} files match their fingerprints.`
                    : "Some records did not verify. See the photos marked below."}
                </p>
              )}
            </section>
            <ol className="pub-photos">
              {page.photos.map((p) => (
                <PhotoCard key={p.view} p={p} file={file} result={results[p.view]} focused={p.view === focus} />
              ))}
            </ol>
            <footer className="pub-foot">
              <p>
                Disclosures are generated by ShootAgent from its edit log and its check of each photo against the camera's own
                exposures. Fingerprint of this publication: <span className="num">{page.root.slice(0, 16)}</span>. Not legal advice.
              </p>
            </footer>
          </>
        )}
      </main>
    </div>
  );
}

function PhotoCard({
  p,
  file,
  result,
  focused,
}: {
  p: PublicPhoto;
  file: (blob: string, w?: number) => string;
  result: VerifyResult | "running" | undefined;
  focused: boolean;
}) {
  const badge = BADGE[p.status] ?? BADGE.unverified!;
  const [showCapture, setShowCapture] = useState(false);
  const alterations = p.changes.filter((c) => c.kind === "alteration");
  const repairs = p.changes.filter((c) => c.kind === "restoration");
  const adjustments = p.changes.filter((c) => c.kind === "adjustment" || c.kind === "overlay");
  const original = p.disclosure ? p.original : null;
  const originalLabel = p.original_kind === "capture" ? "As captured" : "Original";
  return (
    <li className="pub-photo" id={`photo-${p.view}`} data-focused={focused || undefined}>
      <div className="pub-photo-head">
        <h2>
          <span className="num">{p.order}</span> {p.label}
        </h2>
        <span className="pub-badge" data-tone={badge.tone}>
          {staged(p) ? "Virtually staged" : badge.text}
        </span>
      </div>
      <div className="pub-media">
        {original ? (
          <BeforeAfter
            before={file(original.blob, 1600)}
            after={file(p.final.blob, 1600)}
            beforeLabel={originalLabel}
            afterLabel="Listing photo"
            alt={p.label}
          />
        ) : (
          <img
            className="pub-img"
            src={file(showCapture && p.capture ? p.capture.blob : p.final.blob, 1600)}
            alt={`${p.label}${showCapture ? ", as captured" : ""}`}
            loading="lazy"
          />
        )}
      </div>
      {p.disclosure ? <p className="pub-disclosure">{p.disclosure}</p> : <p className="pub-plain">No disclosure needed.</p>}
      <ul className="pub-changes">
        {alterations.map((c, i) => (
          <li key={`a${i}`} data-kind="alteration">
            <b>Changed:</b> {c.what}
            {c.by === "editor" ? " (by the photo editor)" : ""}
          </li>
        ))}
        {repairs.length > 0 && (
          <li data-kind="restoration">
            <b>Repaired:</b> an editing mistake was undone using the camera's own exposures, so this part of the photo matches what
            the camera captured.
          </li>
        )}
        {adjustments.length > 0 && (
          <li data-kind="adjustment">
            <b>Adjusted:</b> {adjustments.map((c) => c.what).join(", ")} (not an alteration).
          </li>
        )}
        {!alterations.length && !repairs.length && !adjustments.length && (
          <li data-kind="none">
            {p.status === "unverified"
              ? "ShootAgent made no changes to this photo."
              : "Exposure and colour as delivered; nothing in the scene was added, removed or changed."}
          </li>
        )}
      </ul>
      <div className="pub-photo-foot">
        {!original && p.capture && (
          <button className="link" onClick={() => setShowCapture((v) => !v)}>
            {showCapture ? "Show the listing photo" : "Show what the camera captured"}
          </button>
        )}
        {original && (
          <a className="link" href={file(original.blob)} target="_blank" rel="noreferrer">
            Open the original
          </a>
        )}
        <span className="pub-record">
          {result === "running" ? (
            <span className="muted">Checking the record…</span>
          ) : result ? (
            <span data-ok={result.ok || undefined} className="pub-check">
              {result.ok ? <BadgeCheck size={13} aria-hidden /> : <CircleAlert size={13} aria-hidden />}
              {result.ok ? `Record verified · ${result.entries} entries, ${result.images} files` : result.problems[0]}
            </span>
          ) : (
            <span className="muted num">
              {p.chain.length} records · {p.chain[p.chain.length - 1]?.hash.slice(0, 10)}
            </span>
          )}
        </span>
      </div>
    </li>
  );
}
