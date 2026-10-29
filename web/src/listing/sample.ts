// Listing details for demos. The address names no street and no number, so it cannot be anyone's home;
// price, rooms and size fit the 7-photo demo house. This file is the only copy: scripts/apply-sample-listing.mjs
// (or `shootagent listing-sample <shoot>`) writes it to a shoot, and the film cards, the narration's fact check,
// the buyer page and the Q&A all read the shoot's listing. Pages that show this listing say "Sample listing".
import type { Listing } from "../api/types";
import sample from "./sample-listing.json";

export const SAMPLE_LISTING: Listing = sample;

export const isSampleListing = (l: Pick<Listing, "address"> | null | undefined) => !!l && l.address === SAMPLE_LISTING.address;
