"""Removal pass rate on real photos: each method in models.remove, judged by models.vision.judge_edit.

    .venv/Scripts/python eval/models_removal.py                      # all methods, all cases (~$1.50)
    .venv/Scripts/python eval/models_removal.py --methods lama+generative --model gpt-image-2.5

Cases are AutoHDR customer photos whose labels say a bin, vehicle or cord must go, plus the editor
request in shoot 6de3da631b ("remove white trash bin on the side") when that shoot exists locally.
The object mask is Grounding DINO + SAM for the instruction's phrase, keeping detections that lie
mostly inside the case's allowed-edit mask. Before/after crops go to eval/out/removal/, a summary to
eval/out/removal/summary.json. Every result is asserted byte-identical outside its reported region.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from shootagent.models import remove, segment, vision

OUT = Path(__file__).parent / "out" / "removal"
CUSTOMER = Path.home() / "datasets" / "autohdr" / "customer" / "cases"

# (case, detector phrase, name for the prompt and the judge)
CASES = [
    ("customer-data-068", "trash can", "trash bin"),
    ("customer-data-069", "trash can", "trash bin"),
    ("customer-data-070", "trash can", "trash bin"),
    ("customer-data-071", "trash can", "trash bin"),
    ("customer-train-010", "car", "car"),
    ("customer-train-023", "car", "car"),
    ("customer-train-032", "car", "car"),
    ("customer-train-039", "car", "car"),
    ("customer-train-001", "cord", "loose cord"),
    ("customer-train-012", "cord", "loose cord"),
    ("customer-train-013", "cord", "loose cord"),
]
METHODS = ["lama", "generative", "lama+generative"]


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def object_mask(img: np.ndarray, phrase: str, allowed: np.ndarray | None) -> tuple[np.ndarray, str]:
    found = segment.find(img, phrase, max_items=6, min_score=0.25)
    if allowed is not None:
        found = [f for f in found if (f.mask & allowed).sum() >= 0.5 * f.mask.sum()]
    if not found:
        if allowed is None or not allowed.any():
            raise SystemExit(f"no {phrase!r} found and no allowed-edit mask to fall back on")
        return allowed, "allowed-edit mask (detector found nothing inside it)"
    return np.logical_or.reduce([f.mask for f in found]), f"{len(found)} x {phrase!r} ({', '.join(f'{f.score:.2f}' for f in found)})"


def load_cases(only: list[str] | None) -> list[dict]:
    cases = []
    for case, phrase, what in CASES:
        if only and case not in only:
            continue
        img = load_rgb(CUSTOMER / case / "input" / "current.png")
        allowed_path = CUSTOMER / case / "answers" / "allowed-edit-mask.png"
        allowed = None
        if allowed_path.exists():
            with Image.open(allowed_path) as im:
                allowed = np.asarray(im.convert("L")) > 127
            if allowed.shape != img.shape[:2]:
                allowed = None  # reframing cases use an output canvas; not used here
        mask, how = object_mask(img, phrase, allowed)
        cases.append({"case": case, "img": img, "mask": mask, "what": what, "mask_from": how})
    shoot = Path("data/shoots/6de3da631b")
    if shoot.exists() and (not only or "6de3da631b" in only):
        from shootagent.store import ShootStore

        st = ShootStore(shoot.parent)
        sh = st.load(shoot.name)
        img = st.get_image(sh.id, sh.view("v01").version("v01.0").blob)
        cases.append({"case": "6de3da631b", "img": img, "mask": st.get_mask(sh.id, "m_1039f3cf"), "what": "white trash bin",
                      "mask_from": "find_regions mask m_1039f3cf from the agent run"})
    segment.unload()
    return cases


def save_pair(path: Path, before: np.ndarray, after: np.ndarray, region: np.ndarray) -> None:
    x0, y0, x1, y1 = vision.judge_crop_box(region)
    pair = np.concatenate([before[y0:y1, x0:x1], after[y0:y1, x0:x1]], axis=1)
    im = Image.fromarray(pair)
    im.thumbnail((2000, 1000), Image.LANCZOS)
    im.save(path, quality=90)


def run_one(c: dict, method: str, model: str | None) -> dict:
    started = time.perf_counter()
    r = remove.remove_object(c["img"], c["mask"], what=c["what"], method=method, model=model)
    total_ms = int((time.perf_counter() - started) * 1000)
    diff = np.abs(r.image.astype(np.int16) - c["img"].astype(np.int16)).max(axis=2)
    outside = int(diff[~r.region].max()) if (~r.region).any() else 0
    assert outside == 0, f"{c['case']} {method}: changed pixels outside the reported region (max {outside})"
    v, u = vision.judge_edit(c["img"], r.image, r.region, f"Remove the {c['what']}.")
    tag = f"{c['case']}_{method.replace('+', '-')}"
    save_pair(OUT / f"{tag}.jpg", c["img"], r.image, r.region)
    return {"case": c["case"], "method": r.method, "model": r.model, "verdict": v.verdict, "reason": v.reason,
            "issues": v.issues, "minor": v.minor, "edit_usd": round(r.usd, 4), "judge_usd": round(u.usd, 4),
            "ms": total_ms, "endpoint_ms": r.ms, "crop": f"{tag}.jpg", "mask_from": c["mask_from"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", nargs="+", default=METHODS, choices=METHODS + ["auto"])
    ap.add_argument("--model", default=None, help="image edit model (default: settings.image_edit_model)")
    ap.add_argument("--cases", nargs="+", default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    cases = load_cases(args.cases)
    for c in cases:
        print(f"{c['case']}: mask {c['mask'].mean():.4f} of frame from {c['mask_from']}")

    # LaMa holds the GPU lock, so local methods run first and in order; API-bound ones run in threads.
    local = [(c, m) for c in cases for m in args.methods if m == "lama"]
    remote = [(c, m) for c in cases for m in args.methods if m != "lama"]
    results = [run_one(c, m, args.model) for c, m in local]
    with ThreadPoolExecutor(args.workers) as pool:
        results += list(pool.map(lambda cm: run_one(cm[0], cm[1], args.model), remote))

    print(f"\n{'case':<20} {'method':<16} {'verdict':<7} {'edit $':>7} {'s':>5}  reason")
    for r in sorted(results, key=lambda r: (r["case"], r["method"])):
        print(f"{r['case']:<20} {r['method']:<16} {r['verdict']:<7} {r['edit_usd']:>7.3f} {r['ms'] / 1000:>5.0f}  {r['reason']}")
    print()
    for m in sorted({r["method"] for r in results}):
        rows = [r for r in results if r["method"] == m]
        passed = sum(r["verdict"] == "pass" for r in rows)
        print(f"{m:<16} pass {passed}/{len(rows)}  edit ${sum(r['edit_usd'] for r in rows):.3f}  judge ${sum(r['judge_usd'] for r in rows):.3f}")
    model_tag = (args.model or "default").replace(".", "_")
    (OUT / f"summary_{model_tag}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
