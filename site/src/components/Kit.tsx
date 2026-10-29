import { useEffect, useRef, useState, type ReactNode } from "react";
import { img } from "../content";
import { useEntrance } from "../lib/motion";

// The listing kit as four sheets that start stacked as one and separate once, the first time a third of the
// figure is in view. The key pins a sheet (click, Enter or Space; again, Escape or a tap elsewhere unpins); a mouse
// over a key previews it. Sheets above the focused one rise and sheets below sink, both emptied to outlines.

type Layer = "mls" | "labelled" | "original" | "record";

const KEY: { id: Layer; name: string; sub: string; detail: ReactNode }[] = [
  {
    id: "mls",
    name: "MLS photos",
    sub: "2048 px, in showing order",
    detail:
      "Upload these. File names start with the showing order, and captions.csv carries each photo’s caption, alt text and disclosure for the photo remarks.",
  },
  {
    id: "labelled",
    name: "Labelled copies",
    sub: "For social posts and print",
    detail:
      "Altered photos carry the disclosure on the image and a QR code to the original, because a post or a flyer has nowhere else to put it.",
  },
  {
    id: "original",
    name: "Unaltered originals",
    sub: "One for each altered photo",
    detail:
      "What the camera captured, or the editor’s photo when only ShootAgent’s own edits changed the scene. AB 723 requires a link to it.",
  },
  {
    id: "record",
    name: "Edit record",
    sub: "SHA-256, brackets to file",
    detail: (
      <>
        Every photo’s chain of fingerprints, from the brackets to the published file. <code>shootagent verify kit.zip</code>{" "}
        re-checks it offline.
      </>
    ),
  },
];
// Bottom to top.
const STACK: Layer[] = ["record", "original", "labelled", "mls"];

function Sheet({ id }: { id: Layer }) {
  if (id === "record")
    return (
      <div className="sheet-body sheet-record">
        {"{ "}
        <span className="k">"step"</span>: <span className="s">"edit"</span>,{"\n  "}
        <span className="k">"op"</span>: <span className="s">"remove_object"</span>,{"\n  "}
        <span className="k">"what"</span>: <span className="s">"grey car removed"</span>,{"\n  "}
        <span className="k">"check"</span>: {"{ "}
        <span className="k">"passed"</span>: <span className="n">true</span>,{"\n    "}
        <span className="k">"outside_max_delta"</span>: <span className="n">0</span>
        {" },\n  "}
        <span className="k">"sha256"</span>: <span className="s">"6f1c…a0e4"</span>,{"\n  "}
        <span className="k">"prev"</span>: <span className="s">"92b7…1d3c"</span>
        {" }"}
      </div>
    );
  if (id === "original")
    return (
      <div className="sheet-body">
        <img src={img("car-before.webp")} alt="" loading="lazy" decoding="async" />
      </div>
    );
  return (
    <div className="sheet-body">
      <img src={img("car-after.webp")} alt="" loading="lazy" decoding="async" />
      {id === "labelled" && (
        <div className="sheet-label">
          <span>
            <b>Digitally altered photo</b>
            Grey car removed.
          </span>
          <img src={img("qr-car.svg")} alt="" loading="lazy" decoding="async" />
        </div>
      )}
    </div>
  );
}

export function Kit() {
  const figure = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState<Layer | null>(null);
  const [preview, setPreview] = useState<Layer | null>(null);
  const entrance = useEntrance(figure, { threshold: 0.35 });
  const focus = preview ?? pinned;
  const at = focus ? STACK.indexOf(focus) : -1;

  useEffect(() => {
    const el = figure.current;
    if (!el) return;
    if (entrance === "waiting") {
      delete el.dataset.exploded;
      return;
    }
    el.dataset.exploded = "";
    if (entrance !== "playing") return;
    // The first separation is slower and staggered; afterwards a selection moves at interface speed.
    el.dataset.exploding = "";
    const t = window.setTimeout(() => delete el.dataset.exploding, 1100);
    return () => {
      window.clearTimeout(t);
      delete el.dataset.exploding;
    };
  }, [entrance]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Escape in a text field belongs to the field.
      if (e.target instanceof Element && e.target.closest("input, textarea")) return;
      if (e.key === "Escape") {
        setPinned(null);
        setPreview(null);
      }
    };
    const onDown = (e: PointerEvent) => {
      if (figure.current?.parentElement?.contains(e.target as Node)) return;
      setPinned(null);
      setPreview(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, []);

  const pin = (id: Layer) => {
    setPinned((p) => (p === id ? null : id));
    setPreview(null);
  };
  const shown = KEY.find((k) => k.id === focus);
  const pinnedKey = KEY.find((k) => k.id === pinned);
  const pinnedText = pinnedKey && typeof pinnedKey.detail === "string" ? pinnedKey.detail : pinnedKey ? "Every photo’s chain of fingerprints, from the brackets to the published file, re-checked offline by shootagent verify." : "";

  return (
    <div className="kit">
      {/* data-exploded is the finished state, in the markup so the prerendered page shows it. */}
      <div className="kit-figure" ref={figure} data-focus={focus ?? undefined} data-exploded="" aria-hidden>
        <div className="kit-iso">
          {STACK.map((id, i) => (
            <div
              key={id}
              className="sheet"
              data-layer={id}
              data-pos={focus ? (i === at ? "focus" : i > at ? "above" : "below") : undefined}
              style={{ "--i": i } as React.CSSProperties}
              onClick={() => pin(id)}
            >
              <Sheet id={id} />
            </div>
          ))}
        </div>
      </div>
      <div className="part-copy">
        <h3>One zip for the listing.</h3>
        <p className="body">
          Everything a listing agent uploads, posts or files. Select a layer to see what it holds.
        </p>
        <div className="kit-key" role="group" aria-label="What the listing kit holds">
          {KEY.map((k, i) => (
            <button
              key={k.id}
              className="key-btn"
              aria-pressed={pinned === k.id}
              data-preview={focus === k.id || undefined}
              onClick={() => pin(k.id)}
              onPointerEnter={(e) => e.pointerType === "mouse" && setPreview(k.id)}
              onPointerLeave={(e) => e.pointerType === "mouse" && setPreview((p) => (p === k.id ? null : p))}
            >
              <span className="key-num">{i + 1}</span>
              <span className="key-name">{k.name}</span>
              <span className="key-sub">{k.sub}</span>
            </button>
          ))}
        </div>
        {/* Shown for the layer in focus, hovered or pinned; announced only when a layer is pinned. */}
        <p className="sr-only" aria-live="polite">
          {pinnedKey ? `${pinnedKey.name}. ` : ""}
          {pinnedKey ? pinnedText : ""}
        </p>
        <div className="kit-detail">
          {shown ? (
            <>
              <b>{shown.name}</b>
              {shown.detail}
            </>
          ) : (
            "Plus the disclosures on their own, the listing remarks with a fair-housing wording check, and the 9:16 reel."
          )}
        </div>
      </div>
    </div>
  );
}
