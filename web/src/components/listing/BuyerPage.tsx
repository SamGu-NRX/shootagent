import { ArrowUp, Check, MessageCircleQuestion, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import type { PublicListing } from "../../api/listing";
import { roomChips } from "../../listing/storyboard";
import { isSampleListing } from "../../listing/sample";
import { useListingApi } from "../../state/listingApi";
import { FilmPlayer, type FilmPlayerHandle } from "./FilmPlayer";
import { COPY, initialLang, saveLang, type Copy, type Lang } from "./i18n";

type Load = { state: "loading" } | { state: "ready"; data: PublicListing } | { state: "error"; error: ApiError };

interface Turn {
  id: number;
  question: string;
  answer: string | null;
  status?: "answered" | "follow_up";
  error?: string;
}

/** Public buyer page: the film first, room chips, Q&A, and Book a tour. Designed for a 390 px phone. */
export function BuyerPage({ slug }: { slug: string }) {
  const api = useListingApi();
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [lang, setLangState] = useState<Lang>(initialLang);
  const [shot, setShot] = useState(0);
  const [booking, setBooking] = useState(false);
  const session = useRef<string | undefined>(undefined);
  const player = useRef<FilmPlayerHandle>(null);
  const chips = useRef<HTMLElement>(null);
  const [chip, setChip] = useState(0);
  const t = COPY[lang];

  // the chip of the room on screen, so the row can keep it in view
  useEffect(() => {
    if (load.state !== "ready" || !load.data.film) return;
    const film = load.data.film;
    const lang0 = film.languages.includes(lang) ? lang : film.languages[0]!;
    const k = roomChips(film.shots, lang0, film.languages[0]!).findIndex((c) => c.shots.includes(shot));
    if (k >= 0) setChip(k);
  }, [shot, load, lang]);

  // Keep the playing room's chip in view by scrolling the chip row only; scrollIntoView would also move the page.
  useEffect(() => {
    const row = chips.current;
    const chipEl = row?.children[chip] as HTMLElement | undefined;
    if (!row || !chipEl || row.scrollWidth <= row.clientWidth) return;
    const left = chipEl.offsetLeft - row.offsetLeft - 16;
    if (left < row.scrollLeft || chipEl.offsetLeft - row.offsetLeft + chipEl.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollTo({ left, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }, [chip]);

  const fetchListing = () => {
    setLoad({ state: "loading" });
    api.publicListing(slug).then(
      (data) => setLoad({ state: "ready", data }),
      (e) => setLoad({ state: "error", error: e instanceof ApiError ? e : new ApiError(0, "error", String(e)) }),
    );
  };
  useEffect(fetchListing, [api, slug]); // eslint-disable-line react-hooks/exhaustive-deps

  const setLang = (l: Lang) => {
    saveLang(l);
    setLangState(l);
  };
  useEffect(() => saveLang(lang), []); // eslint-disable-line react-hooks/exhaustive-deps

  if (load.state === "loading") {
    return (
      <div className="day buyer" aria-busy="true">
        <div className="buyer-film-skel" />
      </div>
    );
  }
  if (load.state === "error") {
    return (
      <div className="day buyer buyer-missing">
        <div className="day-empty" role="alert">
          <h1>{load.error.status === 404 ? t.notFound : t.askError}</h1>
          <p>{load.error.status === 404 ? t.notFoundBody : load.error.message}</p>
          {load.error.status !== 404 && (
            <button className="day-btn" onClick={fetchListing}>
              {t.retry}
            </button>
          )}
        </div>
      </div>
    );
  }

  const { data } = load;
  const film = data.film;
  const filmLang: string = film ? (film.languages.includes(lang) ? lang : film.languages[0]!) : lang;
  const l = data.listing;
  const facts = [l.price, t.bedsBaths(l.beds, l.baths) || null, l.sqft ? t.sqft(l.sqft) : null].filter(Boolean) as string[];
  const photo = (blob: string, w?: number) => api.publicPhoto(slug, blob, w);
  const roomPhoto = (view: string) => data.rooms.find((r) => r.view === view)?.photo;

  return (
    <div className="day buyer">
      <div className="buyer-grid">
        <section className="buyer-film" aria-label={t.film}>
          {film ? (
            <FilmPlayer
              ref={player}
              variant="phone"
              lang={filmLang}
              url={api.publicFilm(slug, filmLang)}
              poster={film.variants[filmLang]?.cover ? api.publicCover(slug, filmLang) : null}
              shots={film.shots}
              frameFor={(i) => {
                const b = film.shots[i] && roomPhoto(film.shots[i]!.view);
                return b ? photo(b, 1080) : null;
              }}
              onShot={setShot}
              labels={t}
            />
          ) : (
            <div className="buyer-nofilm">
              {data.rooms[0] && <img src={photo(data.rooms[0].photo, 1080)} alt="" />}
              <p>{t.noFilm}</p>
            </div>
          )}
          <div className="lang-toggle" role="group" aria-label="Language / Idioma">
            {(["en", "es"] as Lang[]).map((x) => (
              <button key={x} aria-pressed={lang === x} onClick={() => setLang(x)} lang={x}>
                {x.toUpperCase()}
              </button>
            ))}
          </div>
        </section>

        <main className="buyer-main">
          {film && (
            <nav className="room-chips" aria-label={t.rooms} ref={chips}>
              {roomChips(film.shots, filmLang, film.languages[0]!).map((chip, k) => (
                <button
                  key={chip.name}
                  className="room-chip"
                  aria-current={chip.shots.includes(shot) ? "true" : undefined}
                  aria-label={t.jumpTo(chip.name)}
                  onClick={() => {
                    setShot(chip.shots[0]!);
                    setChip(k);
                    player.current?.seek(film.shots[chip.shots[0]!]?.times[filmLang]?.[0] ?? 0);
                  }}
                >
                  {chip.name}
                </button>
              ))}
            </nav>
          )}

          <header className="buyer-head">
            {isSampleListing(l) && <p className="sample-pill">{t.sample}</p>}
            <h1 className="address" tabIndex={-1}>
              {l.address}
            </h1>
            {facts.length > 0 && <p className="facts num">{facts.join(" · ")}</p>}
            {l.headline && <p className="headline">{l.headline}</p>}
            <button className="day-btn day-btn-primary book-inline" onClick={() => setBooking(true)}>
              {t.book}
            </button>
          </header>

          <Ask slug={slug} lang={lang} t={t} session={session} />

          <footer className="buyer-foot">
            <p>{t.aiNote(data.disclosure.ai_clips)}</p>
            <a href={`#/l/${encodeURIComponent(slug)}/disclosure`} className="day-link">
              {t.disclosureLink}
            </a>
          </footer>
        </main>
      </div>

      <div className="book-bar">
        <button className="day-btn day-btn-primary book-cta" onClick={() => setBooking(true)}>
          {t.book}
        </button>
      </div>
      {booking && <BookSheet slug={slug} lang={lang} t={t} session={session.current} onClose={() => setBooking(false)} />}
    </div>
  );
}

function Ask({ slug, lang, t, session }: { slug: string; lang: Lang; t: Copy; session: React.MutableRefObject<string | undefined> }) {
  const api = useListingApi();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || busy) return;
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, question, answer: null }]);
    setQ("");
    setBusy(true);
    try {
      const r = await api.ask(slug, question, lang, session.current);
      session.current = r.session;
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, answer: r.answer, status: r.status } : x)));
    } catch (e) {
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, error: e instanceof Error ? e.message : t.askError } : x)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ask" aria-labelledby="ask-title">
      <h2 id="ask-title">
        <MessageCircleQuestion size={18} aria-hidden /> {t.ask}
      </h2>
      <p className="ask-hint">{t.askHint}</p>
      {turns.length > 0 && (
        <ol className="ask-turns" aria-live="polite">
          {turns.map((x) => (
            <li key={x.id} className="turn">
              <p className="turn-q">{x.question}</p>
              {x.answer == null && !x.error && <p className="turn-wait">{t.asking}</p>}
              {x.answer != null && (
                <div className="turn-a" data-status={x.status}>
                  {x.status === "follow_up" && <span className="turn-tag">{t.followUp}</span>}
                  <p>{x.answer}</p>
                </div>
              )}
              {x.error && (
                <p className="turn-error" role="alert">
                  {x.error}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
      {turns.length === 0 && (
        <div className="ask-examples">
          {t.examples.map((ex) => (
            <button key={ex} className="ask-example" onClick={() => void send(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}
      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          void send(q);
        }}
      >
        <label className="sr-only" htmlFor="ask-input">
          {t.ask}
        </label>
        <input
          id="ask-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t.askPlaceholder}
          maxLength={300}
          enterKeyHint="send"
          autoComplete="off"
        />
        <button type="submit" className="ask-send" disabled={busy || q.trim().length < 3} aria-label={t.askSend}>
          <ArrowUp size={18} aria-hidden />
        </button>
      </form>
    </section>
  );
}

function BookSheet({ slug, lang, t, session, onClose }: { slug: string; lang: Lang; t: Copy; session?: string; onClose: () => void }) {
  const api = useListingApi();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [closing, setClosing] = useState(false);

  // No close() on cleanup: StrictMode's mount-unmount-mount would fire "close" and dismiss the dialog at once.
  // Removing an open dialog from the page ends its modal state anyway.
  useEffect(() => {
    if (!ref.current?.open) ref.current?.showModal();
  }, []);

  const reduced = useMemo(() => matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const close = () => {
    if (reduced) return onClose();
    setClosing(true);
    setTimeout(onClose, 220);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.book(slug, { name, contact, preferred_time: when, language: lang, ...(session ? { session } : {}) });
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog
      ref={ref}
      className="day sheet"
      data-closing={closing || undefined}
      aria-labelledby="book-title"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => e.target === ref.current && close()}
    >
      <div className="sheet-grip" aria-hidden />
      {sent ? (
        <div className="sheet-done">
          <svg className="done-check" viewBox="0 0 52 52" aria-hidden>
            <circle cx="26" cy="26" r="24" />
            <path d="M15 27l7 7 15-16" />
          </svg>
          <h2 id="book-title">{t.sent}</h2>
          <p>{t.sentBody(when)}</p>
          <button className="day-btn day-btn-primary" onClick={close} autoFocus>
            {t.done}
          </button>
        </div>
      ) : (
        <form className="sheet-form" onSubmit={submit}>
          <header className="sheet-head">
            <h2 id="book-title">{t.bookTitle}</h2>
            <button type="button" className="day-icon-btn" onClick={close} aria-label={t.close}>
              <X size={18} />
            </button>
          </header>
          <p className="sheet-intro">{t.bookIntro}</p>
          <label className="day-field">
            <span>{t.name}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoComplete="name" />
          </label>
          <label className="day-field">
            <span>{t.contact}</span>
            <input value={contact} onChange={(e) => setContact(e.target.value)} required maxLength={120} autoComplete="email" />
          </label>
          <label className="day-field">
            <span>{t.when}</span>
            <input value={when} onChange={(e) => setWhen(e.target.value)} required maxLength={120} placeholder={t.whenPlaceholder} />
          </label>
          <div className="when-chips" role="group" aria-label={t.when}>
            {t.whenChips.map((c) => (
              <button type="button" key={c} className="when-chip" aria-pressed={when === c} onClick={() => setWhen(c)}>
                {when === c && <Check size={13} aria-hidden />}
                {c}
              </button>
            ))}
          </div>
          {error && (
            <p className="day-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="day-btn day-btn-primary" disabled={busy || !name.trim() || !contact.trim() || !when.trim()}>
            {busy ? t.sending : t.send}
          </button>
        </form>
      )}
    </dialog>
  );
}

