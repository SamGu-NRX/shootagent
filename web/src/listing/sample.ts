// Listing details for demos. The address names no street and no number, so it cannot be anyone's home;
// price, rooms and size fit the 7-photo demo house. scripts/apply-sample-listing.mjs writes the same file to a
// shoot. Pages that show this listing say "Sample listing".
import type { Listing } from "../api/types";
import sample from "./sample-listing.json";

export const SAMPLE_LISTING: Listing = sample;

export const isSampleListing = (l: Pick<Listing, "address"> | null | undefined) => !!l && l.address === SAMPLE_LISTING.address;
