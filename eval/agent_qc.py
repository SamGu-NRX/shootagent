"""End-to-end QC eval: the full agent (detect -> adjudicate -> fix -> self-check) on AutoHDR defect cases.

Unlike eval/qc_defects.py, which scores the deterministic candidates, this scores what the agent finally
did per case, against labels.json:

  fixed      a finding of the labelled family marked fixed, overlapping a labelled box (or any fixed
             grouping finding for ghosting_grouping, whose boxes cover the whole frame)
  flagged    the right family confirmed but the fix failed its check, or left open for a human
  missed     no confirmed finding of the labelled family
  clean_ok   clean control left byte-identical (head is still the delivered photo)
  wrong_edit an edit kept on a clean control, or on a defect case only outside every labelled box

Usage: python eval/agent_qc.py [--per-shoot 8] [--limit N] [--cases id,id]
Writes eval/out/agent_qc.json and eval/out/agent_qc.md. Spends API credits (~$0.05 per view).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from shootagent.agent import loop
from shootagent.config import get_settings
from shootagent.ingest import create_shoot_from_defect_cases
from shootagent.store import ShootStore
from shootagent.trace import read_events

OUT = Path(__file__).parent / "out"


def overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / area if area > 0 else 0.0


def score_case(label: dict, views: list) -> dict:
    """`views`: the case's view plus any views split from it by regrouping."""
    family = label.get("confirmed_defect_family")
    boxes = [r["box_xyxy_normalized"] for r in label.get("defect_regions", [])]
    findings = [f for v in views for f in v.findings if f.status != "dismissed"]
    edited = any(v.head != v.versions[0].id for v in views) or len(views) > 1
    if label.get("is_clean_control"):
        return {"outcome": "clean_ok" if not edited else "wrong_edit", "family": None}

    def hits(f) -> bool:
        if f.type != family:
            return False
        if family == "ghosting_grouping" or not f.region:
            return True
        return any(overlap(f.region, b) > 0.2 for b in boxes)

    matching = [f for f in findings if hits(f)]
    if any(f.status == "fixed" for f in matching):
        outcome = "fixed"
    elif matching:
        outcome = "flagged"
    else:
        outcome = "missed"
    off_target = [f for f in findings if f.status == "fixed" and not hits(f)]
    return {"outcome": outcome, "family": family, "off_target_fixes": len(off_target)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-shoot", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cases", default="")
    args = ap.parse_args()

    s = get_settings()
    root = Path(s.autohdr_defect_dir)
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))["cases"]
    labels = labels if isinstance(labels, dict) else {c["case_id"]: c for c in labels}
    case_ids = args.cases.split(",") if args.cases else sorted(p.name for p in root.glob("defect_train_*") if p.is_dir())
    if args.limit:
        case_ids = case_ids[: args.limit]
    store = ShootStore(s.shoots_dir)
    OUT.mkdir(exist_ok=True)

    results, runs = [], []
    for i in range(0, len(case_ids), args.per_shoot):
        chunk = case_ids[i : i + args.per_shoot]
        shoot = create_shoot_from_defect_cases(store, f"agent-qc eval {i // args.per_shoot + 1}", [root / c for c in chunk])
        t0 = time.time()
        trace = loop.start_run(store, shoot.id, "qc")
        loop.wait(trace)
        events = read_events(trace.path)
        end = next((e["data"] for e in reversed(events) if e["kind"] == "run_end"), {"ok": False, "usd": 0, "ms": 0})
        retries = sum(e["kind"] == "retry" for e in events)
        checks = [e["data"]["passed"] for e in events if e["kind"] == "check"]
        runs.append({
            "shoot": shoot.id, "run": trace.id, "cases": chunk, "ok": end.get("ok"), "seconds": round(time.time() - t0, 1),
            "usd": end.get("usd", 0), "checks_passed": sum(checks), "checks_failed": len(checks) - sum(checks),
            "retries": retries, "summary": (end.get("summary") or "")[:300],
        })
        print(json.dumps(runs[-1]), flush=True)
        shoot = store.load(shoot.id)
        for cid in chunk:
            views = [v for v in shoot.views if v.meta.get("case_id") == cid]
            r = score_case(labels[cid], views)
            r.update(case=cid, subtype=labels[cid].get("defect_subtype"))
            results.append(r)
            print("  ", cid, r, flush=True)

    total_views = len(results)
    summary = {
        "cases": total_views,
        "crashed_runs": sum(not r["ok"] for r in runs),
        "seconds_per_view": round(sum(r["seconds"] for r in runs) / max(1, total_views), 1),
        "usd_per_view": round(sum(r["usd"] for r in runs) / max(1, total_views), 4),
        "checks_passed": sum(r["checks_passed"] for r in runs),
        "checks_failed": sum(r["checks_failed"] for r in runs),
        "retries": sum(r["retries"] for r in runs),
    }
    by_family: dict[str, dict[str, int]] = {}
    for r in results:
        fam = r["family"] or "clean control"
        d = by_family.setdefault(fam, {})
        d[r["outcome"]] = d.get(r["outcome"], 0) + 1
    (OUT / "agent_qc.json").write_text(json.dumps({"summary": summary, "by_family": by_family, "runs": runs, "cases": results}, indent=1))
    lines = ["| family | cases | fixed | flagged | missed | clean kept | wrong edit |", "|---|---|---|---|---|---|---|"]
    for fam, d in sorted(by_family.items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"| {fam} | {sum(d.values())} | {d.get('fixed', 0)} | {d.get('flagged', 0)} | {d.get('missed', 0)} | {d.get('clean_ok', 0)} | {d.get('wrong_edit', 0)} |")
    lines.append("")
    lines.append(", ".join(f"{k}: {v}" for k, v in summary.items()))
    (OUT / "agent_qc.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
