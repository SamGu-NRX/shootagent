"""End-to-end smoke run of the model adapters on real AutoHDR customer photos.

    .venv/Scripts/python eval/models_smoke.py            # everything (about $0.20 of API calls)
    .venv/Scripts/python eval/models_smoke.py --no-paid  # local models only, no image edits or judge

Finds "trash bin", "car", "table lamp" and "cord" and saves mask overlays to eval/out/; removes the
cord with LaMa and the car with a generative fill; turns the lamp on with a generative fill; judges
each edit with Claude; asserts every edit left pixels outside its region byte-identical.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from shootagent.config import get_settings
from shootagent.models import device as dev
from shootagent.models import genedit, inpaint, segment, vision
from shootagent.models._composite import affected_region, grow

OUT = Path(__file__).parent / "out"


def load(case: str) -> np.ndarray:
    # The repo .env may hold a Git Bash path (/c/Users/...) that Windows Python cannot open, so try the
    # setting first and the standard download location second, and name both if neither exists.
    candidates = [get_settings().autohdr_customer_dir, Path.home() / "datasets" / "autohdr" / "customer"]
    roots = [Path(r) for r in candidates if r is not None and (Path(r) / "cases").is_dir()]
    if not roots:
        raise SystemExit(f"AutoHDR customer dataset not found at any of {candidates}; set AUTOHDR_CUSTOMER_DIR")
    path = roots[0] / "cases" / case / "input" / "current.png"
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def save_small(name: str, img: np.ndarray, long_edge: int = 1600) -> Path:
    s = min(1.0, long_edge / max(img.shape[:2]))
    im = Image.fromarray(img)
    if s < 1.0:
        im = im.resize((round(img.shape[1] * s), round(img.shape[0] * s)), Image.LANCZOS)
    path = OUT / name
    im.save(path, quality=92)
    return path


def overlay(img: np.ndarray, masks: list[np.ndarray]) -> np.ndarray:
    out = img.copy()
    colours = [(255, 40, 40), (40, 160, 255), (40, 220, 90), (255, 200, 0)]
    for i, m in enumerate(masks):
        c = np.array(colours[i % len(colours)], np.float32)
        out[m] = (0.45 * out[m] + 0.55 * c).astype(np.uint8)
    return out


def side_by_side(name: str, before: np.ndarray, after: np.ndarray, region: np.ndarray) -> None:
    x0, y0, x1, y1 = vision.judge_crop_box(region)
    save_small(name, np.concatenate([before[y0:y1, x0:x1], after[y0:y1, x0:x1]], axis=1))


def outside_delta(before: np.ndarray, after: np.ndarray, region: np.ndarray) -> int:
    diff = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2)
    return int(diff[~region].max()) if (~region).any() else 0


class Timer:
    def __init__(self, rows: list, name: str):
        self.rows, self.name = rows, name

    def __enter__(self):
        dev.reset_peak()
        self.t = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            ms = int((time.perf_counter() - self.t) * 1000)
            self.rows.append((self.name, ms, dev.vram_stats().get("peak_allocated_mb", 0)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-paid", action="store_true", help="skip the image edit endpoint and the Claude judge")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    timings: list[tuple[str, int, int]] = []
    usd = 0.0
    verdicts: list[tuple[str, str, str]] = []

    kitchen = load("customer-data-068")  # pedal bin next to the fridge
    house = load("customer-train-010")  # car on the driveway
    bedroom = load("customer-data-066")  # table lamp, cord under the nightstand

    # ---- find --------------------------------------------------------------------------------------------
    found = {}
    for key, img, prompt in [("bin", kitchen, "trash bin"), ("car", house, "car"), ("lamp", bedroom, "table lamp"), ("cord", bedroom, "cord")]:
        segment.find(img, prompt, max_items=3)  # warm-up: first call loads the models
        with Timer(timings, f"find '{prompt}' {img.shape[1]}x{img.shape[0]}"):
            items = segment.find(img, prompt, max_items=3)
        if not items:
            raise SystemExit(f"find('{prompt}') returned nothing on a photo that contains one")
        for f in items:
            assert f.mask.shape == img.shape[:2], f"mask {f.mask.shape} != image {img.shape[:2]}"
        found[key] = items
        path = save_small(f"find_{key}.jpg", overlay(img, [f.mask for f in items]))
        print(f"find {prompt!r}: {[(f.label, round(f.score, 2), round(float(f.mask.mean()), 4)) for f in items]} -> {path}")
    segment.unload()

    # ---- LaMa removal of the cord --------------------------------------------------------------------------
    cord = np.logical_or.reduce([f.mask for f in found["cord"]])
    inpaint.lama_inpaint(bedroom, cord)  # warm-up
    with Timer(timings, "lama_inpaint cord"):
        no_cord = inpaint.lama_inpaint(bedroom, cord)
    region = affected_region(cord, 8, 3)
    delta = outside_delta(bedroom, no_cord, region)
    assert delta == 0, f"LaMa changed pixels outside its region (max delta {delta})"
    side_by_side("lama_cord.jpg", bedroom, no_cord, region)
    print(f"lama cord: outside delta {delta}")

    # Negative control for the judge: LaMa on a large object leaves a dark smear (see inpaint.py).
    bin_mask = found["bin"][0].mask
    with Timer(timings, "lama_inpaint bin (negative control)"):
        no_bin = inpaint.lama_inpaint(kitchen, bin_mask)
    assert outside_delta(kitchen, no_bin, affected_region(bin_mask, 8, 3)) == 0
    side_by_side("lama_bin.jpg", kitchen, no_bin, affected_region(bin_mask, 8, 3))
    inpaint.unload()

    if args.no_paid:
        print_summary(timings, usd, verdicts)
        return 0

    # ---- generative fills ------------------------------------------------------------------------------------
    lamp = found["lamp"][0].mask
    lamp_size = max(np.ptp(np.nonzero(lamp)[0]), np.ptp(np.nonzero(lamp)[1]))
    lamp_area = grow(lamp, int(0.35 * lamp_size))
    with Timer(timings, "generative_edit lamp on"):
        # Light spill is low-frequency: a narrow feather left a visible ring where the glow stopped.
        lit = genedit.generative_edit(
            bedroom, lamp_area, "Turn on the table lamp: the shade glows warm white from inside and casts a soft warm glow on the wall behind it.",
            feather_px=int(0.2 * lamp_size),
        )
    usd += lit.usd
    delta = outside_delta(bedroom, lit.image, grow(lit.mask, int(0.2 * lamp_size)))
    assert delta == 0, f"lamp edit changed pixels outside its region (max delta {delta})"
    side_by_side("gen_lamp.jpg", bedroom, lit.image, lit.mask)
    print(f"gen lamp: {lit.ms} ms endpoint, ~${lit.usd:.4f}, raw {lit.raw_size}, crop {lit.crop_box}, fit {lit.color_fit}, outside delta {delta}")

    car = found["car"][0].mask
    with Timer(timings, "generative_edit remove car"):
        no_car = genedit.generative_edit(
            house, grow(car, 30), "Remove the car and its shadow; show the empty concrete driveway and the garage door behind it.",
            absorb_shadows=True,
        )
    usd += no_car.usd
    delta = outside_delta(house, no_car.image, grow(no_car.mask, 6))
    assert delta == 0, f"car removal changed pixels outside its region (max delta {delta})"
    side_by_side("gen_car.jpg", house, no_car.image, no_car.mask)
    print(f"gen car: {no_car.ms} ms endpoint, ~${no_car.usd:.4f}, outside delta {delta}")

    # ---- judge -----------------------------------------------------------------------------------------------
    for name, before, after, region, intent in [
        ("lama cord", bedroom, no_cord, affected_region(cord, 8, 3), "Remove the loose cord under the nightstand."),
        ("lama bin (expect fail)", kitchen, no_bin, affected_region(bin_mask, 8, 3), "Remove the rubbish bin."),
        ("gen lamp", bedroom, lit.image, lit.mask, "Turn on the table lamp with a warm glow."),
        ("gen car", house, no_car.image, no_car.mask, "Remove the car from the driveway."),
    ]:
        with Timer(timings, f"judge_edit {name}"):
            v, u = vision.judge_edit(before, after, region, intent)
        usd += u.usd
        verdicts.append((name, v.verdict, v.reason + (f" Issues: {v.issues}" if v.issues else "") + (f" Minor: {v.minor}" if v.minor else "")))
        print(f"judge {name}: {v.verdict} ({u.input_tokens} in / {u.output_tokens} out, ${u.usd:.4f}) {v.reason}")

    print_summary(timings, usd, verdicts)
    return 0


def print_summary(timings, usd: float, verdicts) -> None:
    print("\n step                                     ms   peak VRAM MiB")
    for name, ms, peak in timings:
        print(f" {name:<38} {ms:>6}   {peak:>6}")
    print(f"\n total API spend (estimate): ${usd:.4f}" if not math.isnan(usd) else "\n total API spend: unknown (usage missing)")
    for name, verdict, reason in verdicts:
        print(f" {name}: {verdict.upper()}  {reason}")
    print(f" device: {dev.get_device()}  {dev.vram_stats()}")


if __name__ == "__main__":
    sys.exit(main())
