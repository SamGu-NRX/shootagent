import type { CSSProperties } from "react";
import assets from "./assets.json";

export const DEMO_URL = "https://shootagent.sillion.app";
export const GITHUB_URL = "https://github.com/SamGu-NRX/shootagent";

export const ASSETS = assets;

export const img = (name: string) => `/img/${name}`;

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
