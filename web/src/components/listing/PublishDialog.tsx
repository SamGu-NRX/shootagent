import { Check, Copy, ExternalLink, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { buyerUrl, type ListingState } from "../../api/listing";
import type { Listing } from "../../api/types";
import { SAMPLE_LISTING, isSampleListing } from "../../listing/sample";
import { useListingApi } from "../../state/listingApi";
import { useStore } from "../../state/store";
import { Qr } from "./Qr";

interface Props {
  shootId: string;
  listing: Listing;
  state: ListingState;
  onState: (s: ListingState) => void;
  onClose: () => void;
}

const LOCAL = /^(localhost|127\.|0\.0\.0\.0|\[::1\])/;

/** Publish in two steps: confirm the listing details the buyer page leads with, then show the QR code. */
export function PublishDialog({ shootId, listing, state, onState, onClose }: Props) {
  const api = useListingApi();
  const mock = useStore((s) => s.apiMode === "mock");
  const ref = useRef<HTMLDialogElement>(null);
  const [step, setStep] = useState<"details" | "live">(state.published ? "live" : "details");
  const [fields, setFields] = useState({
    address: listing.address ?? "",
    price: listing.price ?? "",
    beds: listing.beds?.toString() ?? "",
    baths: listing.baths?.toString() ?? "",
    sqft: listing.sqft?.toString() ?? "",
    headline: listing.headline ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // No close() on cleanup: StrictMode's mount-unmount-mount would fire "close" and dismiss the dialog at once.
  // Removing an open dialog from the page ends its modal state anyway.
  useEffect(() => {
    if (!ref.current?.open) ref.current?.showModal();
  }, []);

  const set = (k: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement>) => setFields({ ...fields, [k]: e.target.value });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  const publish = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.saveListing(shootId, {
        address: fields.address.trim(),
        price: fields.price.trim() || null,
        beds: num(fields.beds),
        baths: num(fields.baths),
        sqft: num(fields.sqft),
        headline: fields.headline.trim() || null,
      });
      onState(await api.publish(shootId, true));
      setStep("live");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const unpublish = async () => {
    setBusy(true);
    try {
      onState(await api.publish(shootId, false));
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  let url = buyerUrl(state);
  if (url && mock) url = url.replace("/#/", "/?api=mock#/");
  const local = url ? LOCAL.test(new URL(url).hostname) : false;

  const copy = async () => {
    if (!url) return;
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <dialog
      ref={ref}
      className="modal publish"
      aria-labelledby="publish-title"
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      <header className="modal-head">
        <h2 id="publish-title">{step === "live" ? "Your listing is live" : "Publish listing"}</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </header>

      {step === "details" ? (
        <form className="publish-form" onSubmit={publish}>
          <div className="publish-intro">
            <p className="muted">The buyer page leads with these. Buyers can ask about anything here or in the photos.</p>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                setFields({
                  address: SAMPLE_LISTING.address ?? "",
                  price: SAMPLE_LISTING.price ?? "",
                  beds: String(SAMPLE_LISTING.beds ?? ""),
                  baths: String(SAMPLE_LISTING.baths ?? ""),
                  sqft: String(SAMPLE_LISTING.sqft ?? ""),
                  headline: SAMPLE_LISTING.headline ?? "",
                })
              }
            >
              Use sample details
            </button>
          </div>
          {isSampleListing({ address: fields.address }) && (
            <p className="publish-sample" role="note">
              Sample details: the buyer page will say “Sample listing”.
            </p>
          )}
          <label className="field field-wide">
            <span>Street address</span>
            <input value={fields.address} onChange={set("address")} required autoComplete="off" placeholder="Street address, city, state" />
          </label>
          <div className="field-row">
            <label className="field">
              <span>Price</span>
              <input value={fields.price} onChange={set("price")} placeholder="$0" autoComplete="off" />
            </label>
            <label className="field">
              <span>Beds</span>
              <input value={fields.beds} onChange={set("beds")} inputMode="decimal" pattern="[0-9]*[.]?[0-9]*" autoComplete="off" />
            </label>
            <label className="field">
              <span>Baths</span>
              <input value={fields.baths} onChange={set("baths")} inputMode="decimal" pattern="[0-9]*[.]?[0-9]*" autoComplete="off" />
            </label>
            <label className="field">
              <span>Sq ft</span>
              <input value={fields.sqft} onChange={set("sqft")} inputMode="numeric" pattern="[0-9]*" autoComplete="off" />
            </label>
          </div>
          <label className="field field-wide">
            <span>One-line description</span>
            <input value={fields.headline} onChange={set("headline")} maxLength={140} autoComplete="off" placeholder="What a buyer should know first" />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="modal-actions">
            <button type="button" className="btn btn-quiet" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || !fields.address.trim()}>
              {busy ? "Publishing…" : "Publish listing"}
            </button>
          </div>
        </form>
      ) : (
        <div className="publish-live">
          <div className="qr-wrap" data-local={local || undefined}>
            {url && <Qr value={url} label="QR code for the buyer page. Point a phone camera at it." />}
          </div>
          <div className="publish-side">
            <p className="publish-lede">Scan with a phone camera to open the buyer page.</p>
            {url && (
              <div className="url-row">
                <code className="url-text" title={url}>
                  {url.replace(/^https?:\/\//, "")}
                </code>
                <button className="btn btn-sm" onClick={copy}>
                  {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
                  {copied ? "Copied" : "Copy link"}
                </button>
              </div>
            )}
            {local && (
              <p className="publish-warn" role="note">
                This address only opens on this computer. Open ShootAgent from its public URL, or set PUBLIC_BASE_URL, to get a code a phone can scan.
              </p>
            )}
            <p className="publish-meta">
              {state.notifications
                ? "Tour requests go straight to your phone."
                : "Tour requests are saved under Leads. Set NTFY_TOPIC to get them on your phone too."}
            </p>
            <div className="publish-actions">
              {url && (
                <a className="btn" href={url} target="_blank" rel="noreferrer">
                  <ExternalLink size={13} aria-hidden /> Open buyer page
                </a>
              )}
              <button className="btn btn-quiet" onClick={unpublish} disabled={busy}>
                Unpublish
              </button>
            </div>
          </div>
        </div>
      )}
    </dialog>
  );
}
