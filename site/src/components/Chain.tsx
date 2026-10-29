import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { buildRecord, loadFiles, verify, type Check as Result, type Tamper } from "../lib/chain";
import { ASSETS, img, zoomTo } from "../content";
import { Check, Cross } from "./Icons";

const THUMB: Record<string, string | null> = {
  captured: "street-captured.webp",
  delivered: "street-delivered.webp",
  edit: "street-fixed.webp",
  published: "street-fixed.webp",
};

// Each 3:2 thumbnail shows the corner of the street with the stop sign: there in the brackets, gone from the
// editor's photo, back after the edit.
const THUMB_ZOOM = zoomTo(ASSETS.street.sign, ASSETS.street.size, 0.3, 2 / 3);

const MODES: { id: Tamper; label: string }[] = [
  { id: "none", label: "As published" },
  { id: "photo", label: "Photo changed" },
  { id: "record", label: "Photo and record changed" },
];

function Status({ ok, children }: { ok: boolean; children: string }) {
  return (
    <li data-ok={ok}>
      {ok ? <Check size={13} /> : <Cross size={13} />}
      {children}
    </li>
  );
}

export function Chain() {
  const [mode, setMode] = useState<Tamper>("none");
  const [files, setFiles] = useState<Awaited<ReturnType<typeof loadFiles>> | null>(null);
  const [checks, setChecks] = useState<Result[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Announce the verdict only once the reader has changed something, not while they scroll past.
  const [touched, setTouched] = useState(false);
  const choose = (m: Tamper) => {
    setMode(m);
    setTouched(true);
  };
  const figure = useRef<HTMLDivElement>(null);

  // Fetch and fingerprint the photos once the figure comes near the screen, not while the hero loads.
  useEffect(() => {
    const el = figure.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        if (typeof crypto === "undefined" || !crypto.subtle) {
          setError("This browser can only compute fingerprints on a secure (https) page.");
          return;
        }
        loadFiles().then(setFiles, () => setError("The photos could not be loaded to fingerprint them. Reload the page to try again."));
      },
      { rootMargin: "600px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!files) return;
    let live = true;
    (async () => {
      const { entries, served } = await buildRecord(files, mode);
      const result = await verify(entries, served);
      if (live) setChecks(result);
    })();
    return () => {
      live = false;
    };
  }, [files, mode]);

  const broken = checks?.find((c) => !c.hashOk || !c.linkOk || c.fileOk === false);
  const okText = "Every fingerprint matches, from the brackets to the listing photo.";
  const brokenText = broken
    ? `Broken at “${broken.entry.label}”: ${broken.fileOk === false ? "the photo no longer matches its record." : "the record before it was rewritten."}`
    : "";
  // A radio group: one stop in the tab order, arrow keys move the choice.
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = MODES.findIndex((m) => m.id === mode);
    const to = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: MODES.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    const i = (to + MODES.length) % MODES.length;
    choose(MODES[i]!.id);
    e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]")[i]?.focus();
  };
  return (
    <div className="chain-figure" ref={figure}>
      <div className="seg" role="radiogroup" aria-label="What a reader receives" onKeyDown={onKey}>
        {MODES.map((m) => (
          <button key={m.id} role="radio" aria-checked={mode === m.id} tabIndex={mode === m.id ? 0 : -1} onClick={() => choose(m.id)}>
            {m.label}
          </button>
        ))}
      </div>
      <ol className="chain" aria-label="The edit record for the stop-sign photo">
        {(checks ?? [null, null, null, null]).map((c, i) => {
          const key = c?.entry.key ?? `pending-${i}`;
          const ok = c ? c.hashOk && c.linkOk && c.fileOk !== false : true;
          const thumb = c ? THUMB[c.entry.key] : null;
          return (
            <li key={key} className="record" data-ok={ok} data-link={c && !c.linkOk ? "bad" : undefined}>
              <div className="record-thumb" data-empty={!thumb || undefined}>
                {thumb ? <img src={img(thumb)} alt="" style={THUMB_ZOOM} loading="lazy" decoding="async" /> : "…"}
              </div>
              <h4>{c?.entry.label ?? "Loading"}</h4>
              <p>{c?.entry.detail ?? "Fingerprinting the photos"}</p>
              <span className="hash" title={c?.entry.hash}>
                {c ? c.entry.hash.slice(0, 16) : "················"}
              </span>
              {c && (
                <ul>
                  {i > 0 && <Status ok={c.linkOk}>{c.linkOk ? "Links to the record before" : "Doesn’t link to the record before"}</Status>}
                  {c.fileOk !== null && <Status ok={c.fileOk}>{c.fileOk ? "Photo matches" : "Photo has changed"}</Status>}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
      <p className="verdict-line" data-ok={checks && !error ? !broken : undefined}>
        {error ? (
          error
        ) : !checks ? (
          "Computing fingerprints in your browser…"
        ) : broken ? (
          <>
            <Cross size={15} /> {brokenText}
          </>
        ) : (
          <>
            <Check size={15} /> {okText}
          </>
        )}
      </p>
      <p className="sr-only" role="status">
        {touched && checks ? (broken ? brokenText : okText) : ""}
      </p>
    </div>
  );
}
