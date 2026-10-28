import { useStore } from "../state/store";
import { radioKeys } from "./radioKeys";

/** Live server vs in-browser demo data. Explicit, so nobody mistakes mock output for the agent's. */
export function ApiModeSwitch() {
  const mode = useStore((s) => s.apiMode);
  const setMode = useStore((s) => s.setApiMode);
  return (
    <div className="seg" role="radiogroup" aria-label="Data source" onKeyDown={radioKeys}>
      {(["live", "mock"] as const).map((m) => (
        <button
          key={m}
          role="radio"
          aria-checked={mode === m}
          tabIndex={mode === m ? 0 : -1}
          className="seg-btn"
          onClick={() => {
            if (mode === m) return;
            setMode(m);
            location.hash = "#/";
          }}
        >
          {m === "live" ? "Live server" : "Demo data"}
        </button>
      ))}
    </div>
  );
}
