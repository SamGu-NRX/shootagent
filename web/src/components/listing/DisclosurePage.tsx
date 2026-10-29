import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import type { Disclosure, DisclosureChange } from "../../api/listing";
import { attemptReasons } from "../../listing/reasons";
import { useListingApi } from "../../state/listingApi";
import { useStore } from "../../state/store";
import { COPY, initialLang, saveLang, type Copy, type Lang } from "./i18n";

/** A change record in the reader's language; agent-written notes stay as written (English). */
function changeText(c: DisclosureChange, t: Copy): string {
  if (c.kind === "hdr") return t.dHdr(c.exposures);
  if (c.kind === "edit") {
    const label = t.dOps[c.op] ?? c.op.replace(/_/g, " ");
    return c.note ? `${label}: ${c.note}` : `${label}.`;
  }
  return `${t.dFix}: ${c.explanation.replace(/\.$/, "")}.`;
}

type Source = { kind: "public"; slug: string } | { kind: "private"; shootId: string };

/**
 * AB 723 disclosure: each photo in the listing beside its original, what changed, and the film's AI-generated
 * clips. Public under the listing's slug; the passcode side previews it before publishing.
 */
export function DisclosurePage({ source }: { source: Source }) {
  const api = useListingApi();
  const storeApi = useStore((s) => s.api);
  const [lang, setLang] = useState<Lang>(initialLang);
  const [data, setData] = useState<Disclosure | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const t = COPY[lang];

  useEffect(() => {
    const p = source.kind === "public" ? api.publicDisclosure(source.slug) : api.disclosure(source.shootId);
    p.then(setData, (e) => setError(e instanceof ApiError ? e : new ApiError(0, "error", String(e))));
  }, [api, source.kind, source.kind === "public" ? source.slug : source.shootId]); // eslint-disable-line react-hooks/exhaustive-deps

  const img = (blob: string, w: number) =>
    source.kind === "public" ? api.publicPhoto(source.slug, blob, w) : storeApi.blobUrl(source.shootId, blob, w);
  const back = source.kind === "public" ? `#/l/${encodeURIComponent(source.slug)}` : `#/s/${encodeURIComponent(source.shootId)}/launch`;

  if (error) {
    return (
      <div className="day doc">
        <div className="day-empty" role="alert">
          <h1>{error.status === 404 ? t.notFound : t.askError}</h1>
          <p>{error.status === 404 ? t.notFoundBody : error.message}</p>
        </div>
      </div>
    );
  }

  const film = data?.film;
  return (
    <div className="day doc">
      <header className="doc-bar">
        <a className="day-link doc-back" href={back}>
          <ArrowLeft size={15} aria-hidden /> {source.kind === "public" ? t.dBack : "Launch listing"}
        </a>
        <div className="lang-toggle lang-toggle-inline" role="group" aria-label="Language / Idioma">
          {(["en", "es"] as Lang[]).map((x) => (
            <button
              key={x}
              aria-pressed={lang === x}
              lang={x}
              onClick={() => {
                saveLang(x);
                setLang(x);
              }}
            >
              {x.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <main className="doc-main">
        <p className="doc-law">{data?.law ?? "California AB 723"}</p>
        <h1 className="doc-title" tabIndex={-1}>
          {t.dTitle}
        </h1>
        {data?.address && <p className="doc-address">{data.address}</p>}
        <p className="doc-intro">{t.dIntro}</p>

        {!data && <div className="doc-skel" aria-busy="true" />}

        {data && (
          <ol className="pairs">
            {data.photos.map((p) => (
              <li key={p.view} className="pair">
                <h2 className="pair-room">{p.room}</h2>
                <div className="pair-images">
                  <figure>
                    {p.original ? <img src={img(p.original, 960)} alt={`${t.dOriginal}: ${p.room}`} loading="lazy" decoding="async" /> : <div className="pair-missing" />}
                    <figcaption>
                      <strong>{t.dOriginal}</strong> {t.dKind[p.original_kind]}
                    </figcaption>
                  </figure>
                  <figure>
                    <img src={img(p.published, 960)} alt={`${t.dPublished}: ${p.room}`} loading="lazy" decoding="async" />
                    <figcaption>
                      <strong>{t.dPublished}</strong>
                    </figcaption>
                  </figure>
                </div>
                <div className="pair-changes">
                  <h3>{t.dChanges}</h3>
                  {p.changes.length ? (
                    <ul>
                      {p.changes.map((c, i) => (
                        <li key={i}>{changeText(c, t)}</li>
                      ))}
                    </ul>
                  ) : (
                    <p>{t.dUnchanged}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}

        {film && (
          <section className="doc-film" aria-labelledby="doc-film-title">
            <h2 id="doc-film-title">{t.dFilm}</h2>
            {film.ai_clips.length === 0 ? (
              <p>{t.dFilmNone}</p>
            ) : (
              <>
                <p>
                  {t.dFilmSome} <span className="ai-pill">{film.label[lang] ?? film.label.en ?? "AI-generated motion"}</span>
                </p>
                <ul className="clip-list">
                  {film.ai_clips.map((c) => (
                    <li key={c.shot}>
                      <span className="num">{t.dShot(c.shot)}</span> {c.label}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {film.rejected_clips.length > 0 && (
              <details className="doc-rejected">
                <summary>{t.dRejected(film.rejected_clips.length)}</summary>
                <ul>
                  {film.rejected_clips.map((r, i) => (
                    <li key={i}>
                      <span className="num">{t.dShot(r.shot)}</span> {r.label}:{" "}
                      {attemptReasons(r.reasons, { changes: r.changes })
                        .map((x) => x.plain)
                        .join("; ")}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
