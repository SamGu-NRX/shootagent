import { useEffect, useState } from "react";
import { clock } from "../lib/format";

/** Ticking m:ss.s since an epoch-ms start. Re-renders only itself. */
export function LiveClock({ since, frozenMs }: { since: number | null; frozenMs?: number | null }) {
  const [now, setNow] = useState(() => Date.now());
  const running = frozenMs == null && since != null;
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, [running]);
  const ms = frozenMs ?? (since == null ? 0 : now - since);
  return <span className="num">{clock(ms)}</span>;
}
