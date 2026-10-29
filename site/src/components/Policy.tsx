import { useMemo, useState } from "react";
import { classify, type Verdict } from "../lib/policy";

const EXAMPLES = [
  "Remove the trash bins",
  "Fix the crack in the driveway",
  "Remove the power lines",
  "Virtually stage the living room",
  "Make it half a stop brighter",
  "Add a pool to the backyard",
];

const LABEL: Record<Verdict, string> = {
  decline: "Declined",
  disclose: "Allowed, and disclosed",
  overlay: "Allowed, no disclosure",
  exempt: "Allowed, no disclosure",
  unclear: "Left to the agent",
};
const SHORT: Record<Verdict, string> = { decline: "Declined", disclose: "Disclosed", overlay: "Allowed", exempt: "Allowed", unclear: "Agent decides" };

export function Policy() {
  const [text, setText] = useState("Remove the power lines and the trash bins");
  const result = useMemo(() => classify(text), [text]);
  const parts = result.items.filter((i) => i.verdict !== "unclear");
  const lead = parts.find((i) => i.verdict === result.verdict) ?? result.items[0];
  const partly = result.verdict === "decline" && parts.some((i) => i.verdict !== "decline");
  return (
    <div className="policy">
      <div className="policy-field">
        <label htmlFor="policy-input">Ask for an edit</label>
        <input id="policy-input" value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" spellCheck={false} />
      </div>
      <div className="chips" role="group" aria-label="Examples">
        {EXAMPLES.map((e) => (
          <button key={e} className="chip" onClick={() => setText(e)}>
            {e}
          </button>
        ))}
      </div>
      <div className="verdict" aria-live="polite">
        {text.trim() ? (
          <>
            <span className="verdict-pill" data-verdict={result.verdict}>
              {partly ? "Declined in part" : LABEL[result.verdict]}
            </span>
            {lead && <p>{lead.reason}</p>}
            {parts.length > 1 && (
              <ul>
                {parts.map((i, k) => (
                  <li key={k} data-verdict={i.verdict}>
                    <b>{SHORT[i.verdict]}</b>
                    <span>“{i.text}”</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p>Type a request, or pick one above.</p>
        )}
      </div>
    </div>
  );
}
