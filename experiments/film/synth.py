"""Synthetic clips with a known answer, for measuring check_clip.py.

From each test photo it renders a 6 s camera move (push-in or pan, sub-pixel affine warps) and two
corrupted copies of it:
- bad A ("paste"): a door, window or fixture region copied from elsewhere in the same photo fades in at
  a new place over the middle second (2.5-3.5 s) and stays, the way an image-to-video model invents
  structure;
- bad B ("bend"): a thin-plate-spline warp bows one straight window or door edge sideways, growing from
  1.5 s to 4.5 s, the way such a model morphs geometry.
The corruptions are applied in photo coordinates, so they move with the camera like real content.

    python experiments/film/synth.py --dataset C:/Users/sgu07/datasets/autohdr/defect --out experiments/film/out/synth

Writes one mp4 per clip and manifest.json (clip, start photo, expected verdict) for check_clip.py.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator

from clipio import VideoWriter, load_rgb

FPS = 24
SECONDS = 6.0
PUSH_SCALE = 1.15  # push-in ends on a window 1/1.15 the size of the first
PAN_FRAME = 0.8  # pan frame width as a fraction of the photo width (landscape) or height-fit (portrait)
BEND_AMPLITUDE = 0.012  # peak sideways bow of the bent edge, as a fraction of the photo's long edge
FADE = (2.5, 3.5)  # seconds over which the pasted region fades in
BEND = (1.5, 4.5)  # seconds over which the bend grows to full


@dataclass
class Case:
    case_id: str  # defect_train_00NN
    paste_src: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the photo
    paste_dst: tuple[float, float]  # top-left of the pasted copy
    paste_what: str
    bend_line: tuple[tuple[float, float], tuple[float, float]]  # straight edge to bow, end points as fractions
    bend_what: str


# Regions chosen by eye on gridded previews of each photo. 0060 has no door or window in view, so its
# paste is a pendant light (a fixture).
CASES = [
    Case("defect_train_0054", (0.475, 0.30, 0.545, 0.52), (0.745, 0.27), "small window copied onto the wall between the doorways",
         ((0.263, 0.21), (0.263, 0.51)), "right jamb of the left window"),
    Case("defect_train_0055", (0.700, 0.21, 0.755, 0.71), (0.632, 0.21), "door copied onto the pillar beside it",
         ((0.700, 0.22), (0.700, 0.70)), "left edge of the right-hand door"),
    Case("defect_train_0057", (0.300, 0.17, 0.375, 0.74), (0.455, 0.17), "window trim column copied across the centre window",
         ((0.497, 0.18), (0.497, 0.66)), "centre window's vertical mullion"),
    Case("defect_train_0058", (0.020, 0.17, 0.200, 0.64), (0.660, 0.18), "left window copied onto the right wall",
         ((0.400, 0.25), (0.400, 0.62)), "glass edge of the patio door"),
    Case("defect_train_0060", (0.775, 0.06, 0.850, 0.29), (0.470, 0.13), "pendant light copied onto the back wall",
         ((0.311, 0.335), (0.311, 0.505)), "right edge of the right-hand art frame"),
]


def smoothstep(t: float, a: float, b: float) -> float:
    x = min(1.0, max(0.0, (t - a) / (b - a)))
    return x * x * (3 - 2 * x)


def ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))


def out_size(photo: np.ndarray) -> tuple[int, int]:
    h, w = photo.shape[:2]
    return (1280, 720) if w >= h else (720, 1280)


def window(move: str, t: float, pw: int, ph: int, ow: int, oh: int) -> tuple[float, float, float]:
    """(x0, y0, width) of the source window shown at time t; the window has the output's aspect."""
    a = oh / ow
    full_w = min(pw, ph / a)  # largest window of the output aspect that fits the photo
    u = ease(t / SECONDS)
    if move == "push":
        w = full_w / (1 + (PUSH_SCALE - 1) * u)
        return (pw - w) / 2, (ph - w * a) / 2, w
    if move == "pan":
        w = full_w * PAN_FRAME if pw >= ph else min(pw * 0.999, ph * 0.85 / a)
        return (pw - w) * u, (ph - w * a) / 2, w
    raise ValueError(f"unknown move {move!r}")


def bend_field(shape: tuple[int, int], line, amplitude_px: float) -> np.ndarray:
    """Displacement (H, W, 2) that bows `line` perpendicular to itself by up to `amplitude_px` with a
    thin-plate spline pinned to zero on a box around the line, so only the neighbourhood moves."""
    h, w = shape
    (x0, y0), (x1, y1) = [(p[0] * w, p[1] * h) for p in line]
    length = math.hypot(x1 - x0, y1 - y0)
    tx, ty = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -ty, tx
    s = np.linspace(0, 1, 13)
    on_line = np.stack([x0 + s * (x1 - x0), y0 + s * (y1 - y0)], 1)
    disp = np.outer(np.sin(np.pi * s) * amplitude_px, [nx, ny])
    half = 0.35 * length  # box half-width across the line
    bx0, bx1 = min(x0, x1) - half, max(x0, x1) + half
    by0, by1 = min(y0, y1) - half, max(y0, y1) + half
    bx0, by0, bx1, by1 = max(0, bx0), max(0, by0), min(w - 1, bx1), min(h - 1, by1)
    perim = []
    for v in np.linspace(0, 1, 16):
        perim += [(bx0 + v * (bx1 - bx0), by0), (bx0 + v * (bx1 - bx0), by1), (bx0, by0 + v * (by1 - by0)), (bx1, by0 + v * (by1 - by0))]
    perim = np.unique(np.round(np.array(perim), 3), axis=0)  # the four sides share corners
    pts = np.vstack([on_line, perim])
    vals = np.vstack([disp, np.zeros((len(perim), 2))])
    rbf = RBFInterpolator(pts, vals, kernel="thin_plate_spline")
    field = np.zeros((h, w, 2), np.float32)
    ix0, iy0, ix1, iy1 = int(bx0), int(by0), int(bx1) + 1, int(by1) + 1
    yy, xx = np.mgrid[iy0:iy1, ix0:ix1]
    field[iy0:iy1, ix0:ix1] = rbf(np.stack([xx.ravel(), yy.ravel()], 1)).reshape(iy1 - iy0, ix1 - ix0, 2)
    return field


def paste_layer(photo: np.ndarray, src, dst) -> tuple[np.ndarray, np.ndarray]:
    """(patched photo, feathered alpha) for copying box `src` to top-left `dst`, both as fractions."""
    h, w = photo.shape[:2]
    x0, y0, x1, y1 = round(src[0] * w), round(src[1] * h), round(src[2] * w), round(src[3] * h)
    dx, dy = round(dst[0] * w), round(dst[1] * h)
    pw, ph = min(x1 - x0, w - dx), min(y1 - y0, h - dy)
    patched = photo.copy()
    patched[dy:dy + ph, dx:dx + pw] = photo[y0:y0 + ph, x0:x0 + pw]
    alpha = np.zeros((h, w), np.float32)
    feather = max(1, round(0.04 * min(pw, ph)))
    alpha[dy + feather:dy + ph - feather, dx + feather:dx + pw - feather] = 1.0
    alpha = cv2.GaussianBlur(alpha, (0, 0), feather / 2)
    return patched, alpha


def render(photo: np.ndarray, move: str, variant: str, case: Case, path: Path, bend_amplitude: float = BEND_AMPLITUDE) -> None:
    ow, oh = out_size(photo)
    # Work from a copy about 1.25x the output so the affine warp downsamples only a little.
    s = 1.25 * max(ow, oh) / max(photo.shape[:2])
    src = cv2.resize(photo, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ph, pw = src.shape[:2]
    patched = alpha = field = None
    if variant == "paste":
        patched, alpha = paste_layer(src, case.paste_src, case.paste_dst)
    elif variant == "bend":
        field = bend_field((ph, pw), case.bend_line, bend_amplitude * max(pw, ph))
    gy, gx = np.mgrid[0:ph, 0:pw].astype(np.float32)
    n = round(SECONDS * FPS)
    with VideoWriter(path, ow, oh, FPS) as vw:
        for i in range(n):
            t = i / FPS
            img = src
            if variant == "paste" and (k := smoothstep(t, *FADE)) > 0:
                a = (alpha * k)[..., None]
                img = (src * (1 - a) + patched * a).round().astype(np.uint8)
            elif variant == "bend" and (k := smoothstep(t, *BEND)) > 0:
                img = cv2.remap(src, gx - k * field[..., 0], gy - k * field[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            x0, y0, w = window(move, t, pw, ph, ow, oh)
            z = ow / w
            m = np.array([[z, 0, -x0 * z], [0, z, -y0 * z]], np.float32)
            vw.write(cv2.warpAffine(img, m, (ow, oh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, required=True, help="AutoHDR defect dataset folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cases", nargs="*", help="case ids to render (default: all)")
    ap.add_argument("--bend-only", type=float, metavar="AMPLITUDE",
                    help="render only bend clips with this peak bow (fraction of the long edge), for a sensitivity sweep")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in CASES:
        if args.cases and case.case_id not in args.cases:
            continue
        cdir = args.dataset / case.case_id
        meta = json.loads((cdir / "case.json").read_text())
        photo_path = (cdir / Path(meta["current_image"]).name).resolve()
        photo = load_rgb(photo_path)
        short = case.case_id[-4:]
        for move in ("push", "pan"):
            variants = (("good", "pass", ""), ("paste", "fail", case.paste_what), ("bend", "fail", case.bend_what))
            if args.bend_only:
                variants = (("bend", "fail", case.bend_what),)
            for variant, expected, what in variants:
                suffix = f"{variant}{args.bend_only * 1000:g}" if args.bend_only else variant
                clip = (args.out / f"{short}_{move}_{suffix}.mp4").resolve()
                render(photo, move, variant, case, clip, args.bend_only or BEND_AMPLITUDE)
                manifest.append({"clip": str(clip), "start": str(photo_path), "expected": expected, "case": case.case_id,
                                 "move": move, "variant": variant, "corruption": what})
                print(f"wrote {clip.name}", flush=True)
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
