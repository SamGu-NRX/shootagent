import { useCallback, useRef, useState } from "react";

/**
 * Two aligned images with a draggable divider: `before` on the left, `after` on the right. Keyboard: arrow keys
 * move the divider (role="slider"). Both images must share a frame; ShootAgent registers the camera's capture
 * onto the delivered photo's frame so they do.
 */
export function BeforeAfter({
  before,
  after,
  beforeLabel,
  afterLabel,
  alt,
}: {
  before: string;
  after: string;
  beforeLabel: string;
  afterLabel: string;
  alt: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState(0.5);
  const drag = useRef(false);

  const at = useCallback((clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || !r.width) return;
    setPos(Math.min(1, Math.max(0, (clientX - r.left) / r.width)));
  }, []);

  return (
    <div
      className="ba"
      ref={box}
      onPointerDown={(e) => {
        drag.current = true;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        at(e.clientX);
      }}
      onPointerMove={(e) => drag.current && at(e.clientX)}
      onPointerUp={() => (drag.current = false)}
      onPointerCancel={() => (drag.current = false)}
    >
      <img className="ba-img" src={after} alt={`${alt}, ${afterLabel.toLowerCase()}`} draggable={false} />
      <div className="ba-clip" style={{ clipPath: `inset(0 ${(1 - pos) * 100}% 0 0)` }} aria-hidden>
        <img className="ba-img" src={before} alt="" draggable={false} />
      </div>
      <span className="ba-label ba-label-l" aria-hidden>
        {beforeLabel}
      </span>
      <span className="ba-label ba-label-r" aria-hidden>
        {afterLabel}
      </span>
      <div className="ba-line" style={{ left: `${pos * 100}%` }} aria-hidden />
      <div
        className="ba-handle"
        style={{ left: `${pos * 100}%` }}
        role="slider"
        tabIndex={0}
        aria-label={`Compare ${beforeLabel.toLowerCase()} with ${afterLabel.toLowerCase()}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pos * 100)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            setPos((p) => Math.min(1, Math.max(0, p + (e.key === "ArrowLeft" ? -0.05 : 0.05))));
          }
        }}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
          <path d="M6 3 2 8l4 5M10 3l4 5-4 5" stroke="currentColor" strokeWidth="1.6" fill="none" />
        </svg>
      </div>
    </div>
  );
}
