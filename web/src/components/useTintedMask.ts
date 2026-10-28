import { useEffect, useState } from "react";

const cache = new Map<string, string>();

/**
 * Masks are single-channel PNGs (255 = inside). CSS masking reads alpha, not luminance, so the PNG is
 * converted once into a tinted image whose alpha is the mask value.
 */
export function useTintedMask(url: string | null): string | null {
  const [out, setOut] = useState<string | null>(url ? (cache.get(url) ?? null) : null);
  useEffect(() => {
    if (!url) {
      setOut(null);
      return;
    }
    const hit = cache.get(url);
    if (hit) {
      setOut(hit);
      return;
    }
    let live = true;
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const g = c.getContext("2d", { willReadFrequently: true })!;
      g.drawImage(img, 0, 0);
      const data = g.getImageData(0, 0, c.width, c.height);
      const px = data.data;
      // --agent violet fill at 40%, plus an opaque edge so the mask reads on any photo
      const w = c.width;
      const src = new Uint8Array(px.length / 4);
      for (let i = 0; i < src.length; i++) src[i] = px[i * 4]! > 127 ? 1 : 0;
      const edgeW = Math.max(1, Math.round(w / 500));
      for (let i = 0; i < src.length; i++) {
        const x = i % w;
        let edge = false;
        if (src[i]) {
          for (let d = 1; d <= edgeW && !edge; d++) {
            edge = x - d < 0 || x + d >= w || !src[i - d] || !src[i + d] || !src[i - d * w] || !src[i + d * w];
          }
        }
        const o = i * 4;
        if (edge) {
          px[o] = 225;
          px[o + 1] = 218;
          px[o + 2] = 255;
          px[o + 3] = 255;
        } else {
          const a = px[o]!;
          px[o] = 165;
          px[o + 1] = 147;
          px[o + 2] = 255;
          px[o + 3] = a * 0.4;
        }
      }
      g.putImageData(data, 0, 0);
      const result = c.toDataURL("image/png");
      cache.set(url, result);
      if (live) setOut(result);
    };
    img.src = url;
    return () => {
      live = false;
    };
  }, [url]);
  return out;
}
