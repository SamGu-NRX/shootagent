import { useMemo } from "react";
import { encode } from "uqr";

/** QR code as one SVG path. Quiet zone of 2 modules; ECC "M" survives a glare spot on a projector screen. */
export function Qr({ value, label }: { value: string; label: string }) {
  const { d, size } = useMemo(() => {
    const qr = encode(value, { ecc: "M", border: 2 });
    let path = "";
    qr.data.forEach((row, y) =>
      row.forEach((on, x) => {
        if (on) path += `M${x} ${y}h1v1h-1z`;
      }),
    );
    return { d: path, size: qr.size };
  }, [value]);
  return (
    <svg className="qr" viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges" role="img" aria-label={label}>
      <rect width={size} height={size} fill="#fff" />
      <path d={d} fill="#111" />
    </svg>
  );
}
