"""Evaluate qc.analyze_view on the AutoHDR defect set.

Each case is ingested into a scratch store and analysed through the same path the agent uses. Labels are
read here only. Metrics:

* detection recall per family: the case has a candidate of the labelled family whose box overlaps a
  labelled box (intersection >= 25% of either box). Reported for all candidates and for candidates at or
  above HIGH_CONFIDENCE.
* clean controls: number of high-confidence candidates (should be 0).
* unmatched high-confidence candidates on defect cases (other families or wrong place). Labels are not an
  exhaustive inventory, so some of these may be real; they bound precision from below.
* grouping accuracy: predicted bracket groups equal `coherent_groups` (restricted to labelled indices).
* seconds per view (analysis incl. image IO; ingest excluded).

    python eval/qc_defects.py                     # all cases
    python eval/qc_defects.py --cases 0044 0029   # a subset
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shootagent.config import get_settings
from shootagent.ingest import ingest_autohdr_defect_case, resolve_path
from shootagent.qc import HIGH_CONFIDENCE, analyze_view
from shootagent.store import ShootStore

OUT = Path(__file__).resolve().parent / "out"


def overlap(a, b) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    area = lambda r: max(1e-9, (r[2] - r[0]) * (r[3] - r[1]))
    return max(inter / area(a), inter / area(b))


def matches(c: dict, family: str, boxes: list) -> bool:
    return c["type"] == family and any(overlap(c["region"], b) >= 0.25 for b in boxes)


def norm_groups(groups, keep: set[int]) -> list[tuple[int, ...]]:
    out = [tuple(sorted(i for i in g if i in keep)) for g in groups]
    return sorted(g for g in out if g)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", help="case ids or 4-digit suffixes")
    ap.add_argument("--keep-store", action="store_true", help="keep the scratch store for inspection")
    a = ap.parse_args()
    root = resolve_path(get_settings().autohdr_defect_dir, "AUTOHDR_DEFECT_DIR")
    labels = json.loads((root / "labels.json").read_text(encoding="utf-8"))["cases"]
    ids = sorted(labels)
    if a.cases:
        want = {c if c.startswith("defect_") else f"defect_train_{c}" for c in a.cases}
        ids = [i for i in ids if i in want]

    tmp = Path(tempfile.mkdtemp(prefix="qc_eval_"))
    store = ShootStore(tmp)
    shoot = store.create("qc eval")
    rows = []
    for cid in ids:
        lab = labels[cid]
        family = lab.get("confirmed_defect_family") or "clean"
        boxes = [r["box_xyxy_normalized"] for r in (lab.get("defect_regions") or [])]
        ba = lab.get("bracket_assessment") or {}
        if isinstance(ba, str):
            ba = json.loads(ba)
        vid = ingest_autohdr_defect_case(store, shoot.id, root / cid)
        t = time.perf_counter()
        try:
            va = analyze_view(store, shoot.id, vid, None)
            err = None
        except Exception as e:  # recorded, the run continues
            va, err = None, f"{type(e).__name__}: {e}"
        secs = time.perf_counter() - t
        cands = va.candidates if va else []
        hit_any = any(matches(c, family, boxes) for c in cands)
        hit_high = any(matches(c, family, boxes) and c["confidence"] >= HIGH_CONFIDENCE for c in cands)
        high = [c for c in cands if c["confidence"] >= HIGH_CONFIDENCE]
        unmatched_high = [c for c in high if not matches(c, family, boxes)]
        gt_groups = ba.get("coherent_groups")
        group_ok = None
        if gt_groups and va:
            keep = {i for g in gt_groups for i in g}
            group_ok = norm_groups(va.groups, keep) == norm_groups(gt_groups, keep)
        rows.append({
            "case": cid, "family": family, "subtype": lab.get("defect_subtype"), "clean": bool(lab.get("is_clean_control")),
            "hit_any": hit_any, "hit_high": hit_high, "n_candidates": len(cands), "n_high": len(high),
            "unmatched_high": [{"type": c["type"], "confidence": c["confidence"], "region": c["region"]} for c in unmatched_high],
            "top": [{"type": c["type"], "confidence": c["confidence"], "region": c["region"], "kind": c["kind"]} for c in cands[:5]],
            "groups_pred": va.groups if va else None, "groups_gt": gt_groups, "group_ok": group_ok,
            "seconds": round(secs, 2), "error": err,
        })
        print(f"{cid} {family:22s} hit={hit_any!s:5} high={hit_high!s:5} n_high={len(high)} "
              f"groups_ok={group_ok} {secs:.1f}s" + (f" ERROR {err}" if err else ""), flush=True)

    fam = defaultdict(lambda: {"cases": 0, "hit_any": 0, "hit_high": 0})
    for r in rows:
        if r["clean"]:
            continue
        f = fam[r["family"]]
        f["cases"] += 1
        f["hit_any"] += r["hit_any"]
        f["hit_high"] += r["hit_high"]
    clean = [r for r in rows if r["clean"]]
    defect = [r for r in rows if not r["clean"]]
    grouped = [r for r in rows if r["group_ok"] is not None]
    multi = [r for r in grouped if len(r["groups_gt"]) > 1]
    secs = sorted(r["seconds"] for r in rows)
    summary = {
        "cases": len(rows),
        "high_confidence_threshold": HIGH_CONFIDENCE,
        "families": dict(sorted(fam.items())),
        "defect_recall_any": round(sum(r["hit_any"] for r in defect) / max(1, len(defect)), 3),
        "defect_recall_high": round(sum(r["hit_high"] for r in defect) / max(1, len(defect)), 3),
        "clean_controls": len(clean),
        "clean_with_high_candidates": sum(r["n_high"] > 0 for r in clean),
        "clean_high_candidates_total": sum(r["n_high"] for r in clean),
        "defect_unmatched_high_total": sum(len(r["unmatched_high"]) for r in defect),
        "grouping_accuracy": round(sum(r["group_ok"] for r in grouped) / max(1, len(grouped)), 3),
        "grouping_cases": len(grouped),
        "grouping_accuracy_multi_view": round(sum(r["group_ok"] for r in multi) / max(1, len(multi)), 3),
        "grouping_multi_view_cases": len(multi),
        "seconds_median": secs[len(secs) // 2] if secs else None,
        "seconds_max": secs[-1] if secs else None,
        "errors": [r["case"] for r in rows if r["error"]],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    name = "qc_defects" if not a.cases else "qc_defects_subset"
    (OUT / f"{name}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1), encoding="utf-8")
    md = ["| family | cases | detected (any confidence) | detected (>= %.2f) |" % HIGH_CONFIDENCE, "|---|---|---|---|"]
    for k, v in sorted(fam.items()):
        md.append(f"| {k} | {v['cases']} | {v['hit_any']} | {v['hit_high']} |")
    md += ["", f"- Defect recall: {summary['defect_recall_any']:.0%} any confidence, {summary['defect_recall_high']:.0%} high confidence.",
           f"- Clean controls with a high-confidence candidate: {summary['clean_with_high_candidates']} of {len(clean)}.",
           f"- High-confidence candidates not matching a label on defect cases: {summary['defect_unmatched_high_total']}.",
           f"- Grouping matches `coherent_groups`: {summary['grouping_accuracy']:.0%} of {len(grouped)} cases "
           f"({summary['grouping_accuracy_multi_view']:.0%} of {len(multi)} multi-view cases).",
           f"- Seconds per view: median {summary['seconds_median']}, max {summary['seconds_max']}."]
    (OUT / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    if a.keep_store:
        print(f"store kept at {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
