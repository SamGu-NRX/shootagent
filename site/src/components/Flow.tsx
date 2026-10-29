import { useEffect, useLayoutEffect, useRef } from "react";
import { useEntrance } from "../lib/motion";
import { Chat, CheckCircle, Film, Layers, Package, Replay, Scan } from "./Icons";

const NODES = [
  { icon: Layers, title: "Brackets in", text: "Every exposure the camera took, and the editor’s finished photo." },
  { icon: Scan, title: "QC", text: "Where the photo disagrees with the brackets, the agent looks and decides.", agent: true },
  { icon: CheckCircle, title: "Self-check", text: "Nothing outside the mask may change, and a vision judge must agree.", loop: true },
  { icon: Chat, title: "Chat", text: "The customer’s changes, photo by photo, with a reason for every skip.", agent: true },
  { icon: Film, title: "Reel", text: "A 9:16 walkthrough in showing order, with the music generated." },
  { icon: Package, title: "Deliver", text: "Disclosures, the originals, a record anyone can check, and a kit." },
];

export function Flow() {
  const flow = useRef<HTMLOListElement>(null);
  const entrance = useEntrance(flow, { threshold: 0, rootMargin: "0px 0px -20% 0px" });
  useLayoutEffect(() => {
    if (flow.current) flow.current.dataset.shown = "";
  }, []);
  useEffect(() => {
    const el = flow.current;
    if (!el) return;
    if (entrance === "waiting") delete el.dataset.shown;
    else el.dataset.shown = "";
  }, [entrance]);
  return (
    <section className="section" aria-labelledby="flow-title">
      <div className="wrap">
        <div className="section-head">
          <h2 id="flow-title">Five passes over every shoot.</h2>
          <p className="lede">
            The agent decides; deterministic code and pinned models make the pixels. The loop, not the model, enforces the
            rules: a plan first, a check after every edit, and no edit the listing photo policy declines.
          </p>
        </div>
        <ol className="flow" ref={flow}>
          {NODES.map((n) => (
            <li key={n.title} className="node" data-agent={n.agent || undefined}>
              <span className="node-icon">
                <n.icon />
              </span>
              <h3>{n.title}</h3>
              <p>{n.text}</p>
              {n.loop && (
                <span className="loop">
                  <Replay size={13} /> Fails: undo, try another tool
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
