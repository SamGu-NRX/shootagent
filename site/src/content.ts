import type { CSSProperties } from "react";
import assets from "./assets.json";

export const DEMO_URL = "https://shootagent.sillion.app";
export const GITHUB_URL = "https://github.com/SamGu-NRX/shootagent";
export const LICENSE_URL = `${GITHUB_URL}/blob/main/LICENSE`;
// The compliance tools (disclosures, the edit record, the listing kit, the policy) until they reach the live demo.
export const PR_COMPLIANCE_URL = `${GITHUB_URL}/pull/1`;

export const ASSETS = assets;

export const img = (name: string) => `/img/${name}`;

// The widths each photo exists in (site/scripts/prepare_images.py writes <stem>-<width>.webp beside the full size).
const WIDTHS: Record<string, number[]> = {
  "street-delivered.webp": [720, 1080, 1440],
  "street-captured.webp": [720, 1080, 1440],
  "street-fixed.webp": [720, 1080, 1440],
  "laundry-delivered.webp": [1120, 2240],
  "laundry-a-band.webp": [1120, 2240],
};
/**
 * The street photos' display size wherever they appear (the hero and the viewfinder): the page's width below
 * 900 px, at most 680 px beside the text above. One value, so each file downloads once (index.html preloads the
 * hero's with the same sizes).
 */
export const STREET_SIZES = "(max-width: 900px) 100vw, 680px";

/** A srcset for a photo that has smaller copies, so each screen downloads one near the size it shows. */
export function srcSet(name: string): string | undefined {
  const widths = WIDTHS[name];
  if (!widths) return undefined;
  const largest = widths[widths.length - 1];
  return widths.map((w) => `${w === largest ? img(name) : img(`${name.slice(0, -5)}-${w}.webp`)} ${w}w`).join(", ");
}

/**
 * CSS variables (--zw, --zx, --zy) that place a photo inside a cell so the cell shows `region` (a share of the
 * photo's width) centred on `box`. `cell` is the cell's height over its width.
 */
export function zoomTo(box: number[], size: { w: number; h: number }, region: number, cell: number): CSSProperties {
  const cx = (box[0]! + box[2]!) / 2;
  const cy = (box[1]! + box[3]!) / 2;
  return {
    "--zw": `${(100 / region).toFixed(2)}%`,
    "--zx": `${((-(cx - region / 2) / region) * 100).toFixed(2)}%`,
    "--zy": `${(50 - ((cy * size.h) / size.w / region / cell) * 100).toFixed(2)}%`,
  } as CSSProperties;
}
