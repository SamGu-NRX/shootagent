import { useEffect, useRef, useState } from "react";

interface Layer {
  src: string;
  ready: boolean;
}

/**
 * Holds the previous image until the next one has decoded, then crossfades (or swaps instantly).
 * Prevents the blank flash a plain src change causes when moving between versions.
 */
export function SwapImage({
  src,
  instant,
  alt,
  pixelated,
  onSize,
  onError,
}: {
  src: string | null;
  instant: boolean;
  alt: string;
  pixelated?: boolean;
  onSize?: (size: { w: number; h: number }) => void;
  onError?: (src: string) => void;
}) {
  const [layers, setLayers] = useState<Layer[]>(src ? [{ src, ready: false }] : []);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!src) {
      setLayers([]);
      return;
    }
    setLayers((ls) => {
      const existing = ls.find((l) => l.src === src);
      const keep = ls.filter((l) => l.src !== src && l.ready).slice(-1);
      return [...keep, existing ?? { src, ready: false }];
    });
  }, [src]);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const loaded = (img: HTMLImageElement) => {
    const s = img.dataset.src!;
    onSize?.({ w: img.naturalWidth, h: img.naturalHeight });
    setLayers((ls) => ls.map((l) => (l.src === s ? { ...l, ready: true } : l)));
    const drop = () => setLayers((ls) => (ls[ls.length - 1]?.src === s ? ls.slice(-1) : ls));
    if (timer.current) clearTimeout(timer.current);
    if (instant) drop();
    else timer.current = setTimeout(drop, 220);
  };

  return (
    <>
      {layers.map((l, i) => (
        <img
          key={l.src}
          data-src={l.src}
          src={l.src}
          alt={i === layers.length - 1 ? alt : ""}
          aria-hidden={i !== layers.length - 1}
          draggable={false}
          decoding="async"
          className="swap-img"
          data-ready={l.ready}
          data-instant={instant}
          data-pixelated={pixelated || undefined}
          ref={(img) => {
            if (img && img.complete && img.naturalWidth && !l.ready) loaded(img);
          }}
          onLoad={(e) => !l.ready && loaded(e.currentTarget)}
          onError={() => onError?.(l.src)}
        />
      ))}
    </>
  );
}
