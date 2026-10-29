"""Prepare the landing page's customer-request pairs from the README's before/after composites.

Run from the repository root (needs only Pillow, numpy, SciPy and scikit-image):

    uv run python site/scripts/prepare_edits.py

docs/media/editor-requests-before-after.jpg and docs/media/removals-before-after.jpg hold the seven saved customer
requests from AutoHDR's editor dataset that README.md's chat-pass table reports, each as a before panel (left) and
an after panel (right) on white. For every pair this:

* finds the two panels' exact pixel boxes from the composite itself: white gutter rows split it into bands, white
  gutter columns split each band into two panels of equal width, and the after panel must line up with the before
  panel at zero offset (any one-pixel shift matches worse);
* crops both panels to WebP at their native size, as prepare_images.py does for the walls and the car;
* measures where the edit changed the photo (colour difference in Lab, above the pair's own JPEG noise), and saves
  that as a PNG whose alpha is the change, plus its outline as an SVG path;
* reads each request's text, result, attempts, time and cost from README.md's chat-pass table.

Output: site/public/img/edits/<name>-before.webp, <name>-after.webp, <name>-mask.png, and site/src/edits.json.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage
from skimage.color import rgb2lab
from skimage.filters import apply_hysteresis_threshold
from skimage.measure import approximate_polygon, find_contours

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site" / "public" / "img" / "edits"
# The page imports the numbers; Vite only bundles JSON from src/.
DATA = ROOT / "site" / "src" / "edits.json"
README = ROOT / "README.md"
EDITOR = "docs/media/editor-requests-before-after.jpg"
REMOVALS = "docs/media/removals-before-after.jpg"
QUALITY = 84


@dataclass(frozen=True)
class Pair:
    name: str
    composite: str
    band: int  # which row of the composite, from the top
    request: int  # the request's number in README.md's chat-pass table
    kind: str  # "Recolour", "Add" or "Remove": how the mask is cleaned up, and the tab's tag
    alt_before: str
    alt_after: str
    why: str | None = None  # why it took more than one attempt, from README.md


PAIRS = [
    Pair("floors", EDITOR, 0, 156, "Recolour",
         "A long white hallway with peach floor tiles.",
         "The same hallway with the floor tiles in #C8C2B7, a pale warm grey.",
         "The vision judge rejected the first recolour as too slight."),
    Pair("tv", EDITOR, 1, 100, "Add",
         "A bedroom with a blank black TV above a white dresser.",
         "The same bedroom with the reference photo, an apartment building, on the TV screen."),
    Pair("walls", EDITOR, 2, 21, "Recolour",
         "A bedroom with dark grey walls on both sides.",
         "The same bedroom with both walls in the reference teal."),
    Pair("bin", REMOVALS, 0, 43, "Remove",
         "A bedroom corner with a white trash bin beside the dresser.",
         "The same corner without the bin: wall, skirting and floor in its place."),
    Pair("car", REMOVALS, 1, 5, "Remove",
         "A brick driveway with a grey car parked in front of a carport.",
         "The same driveway with the car gone and the carport behind it filled in."),
    Pair("wires", REMOVALS, 2, 147, "Remove",
         "A bedroom wall with a power strip, an extension cord and wires along the skirting.",
         "The same wall without the cord or the wires; the outlet is still there.",
         "The vision judge rejected the first two removals for erasing the wall outlet; the third kept it."),
    Pair("lockbox", REMOVALS, 3, 58, "Remove",
         "A front door with a key lockbox hanging from its handle.",
         "The same front door without the lockbox."),
]


# ---------- panel geometry -------------------------------------------------------------------------------------------

def runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Runs of True as half-open (start, end) pairs."""
    edges = np.diff(np.concatenate([[0], flags.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist()))


def is_gutter(lines: np.ndarray, axis: int) -> np.ndarray:
    """A gutter line is white across the composite: JPEG ringing beside a photo dims it a little, never to photo
    levels, so 95% of it stays above 220 and its median above 240 (on each pixel's darkest channel)."""
    return (np.percentile(lines, 5, axis=axis) >= 220) & (np.median(lines, axis=axis) >= 240)


def find_pairs(img: np.ndarray) -> list[tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]:
    """[(rows, before columns, after columns)] for each band of the composite, top to bottom."""
    darkest = img.min(axis=2)
    found = []
    for y0, y1 in runs(~is_gutter(darkest, axis=1)):
        if y1 - y0 < 50:
            continue
        panels = [c for c in runs(~is_gutter(darkest[y0:y1], axis=0)) if c[1] - c[0] >= 50]
        if len(panels) != 2 or panels[0][1] - panels[0][0] != panels[1][1] - panels[1][0]:
            raise SystemExit(f"rows {y0}-{y1}: expected two panels of equal width, found {panels}")
        found.append(((y0, y1), panels[0], panels[1]))
    return found


def check_registration(before: np.ndarray, after: np.ndarray) -> float:
    """The after panel is the same photo as the before panel, so it must match best at zero offset."""
    b, a = before.astype(np.int16), after.astype(np.int16)
    core = a[2:-2, 2:-2]

    def mismatch(dy: int, dx: int) -> float:
        return float(np.abs(b[2 + dy:b.shape[0] - 2 + dy, 2 + dx:b.shape[1] - 2 + dx] - core).mean())

    at_zero = mismatch(0, 0)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dy or dx) and mismatch(dy, dx) <= at_zero:
                raise SystemExit(f"the panels match better shifted by ({dx}, {dy}) px; the geometry is off")
    return at_zero


# ---------- change mask ----------------------------------------------------------------------------------------------

def disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return np.hypot(y, x) <= r + 0.01


def fill_pockets(mask: np.ndarray, max_share: float) -> np.ndarray:
    """Fill small unchanged pockets, including those closed off by the frame's edge (a bin's lid as white as the
    wall behind it leaves a notch where the bin meets the edge)."""
    padded = np.pad(mask, 1, constant_values=True)
    background, n = ndimage.label(~padded)
    sizes = np.bincount(background.ravel(), minlength=n + 1)
    small = np.flatnonzero(sizes < max_share * mask.size)
    padded |= np.isin(background, small[small > 0])
    return padded[1:-1, 1:-1]


def change_mask(before: np.ndarray, after: np.ndarray, kind: str) -> np.ndarray:
    """Where the edit changed the photo. Both panels went through the same JPEG, so outside the edit they differ
    only by its noise: the colour difference (CIE76 in Lab, after a light blur) is thresholded relative to the pair's
    own noise (median and MAD), with hysteresis so faint parts of a change join a clear core; specks are opened
    away, and only regions of real size are kept. A recolour keeps its crisp edges; an added or removed object is
    closed and smoothed into its footprint."""
    lab = [rgb2lab(ndimage.gaussian_filter(x / 255.0, (1.2, 1.2, 0))) for x in (before, after)]
    diff = np.sqrt(((lab[0] - lab[1]) ** 2).sum(axis=-1))
    median = float(np.median(diff))
    mad = 1.4826 * float(np.median(np.abs(diff - median)))
    mask = apply_hysteresis_threshold(diff, max(2.5, median + 5 * mad), max(5.0, median + 10 * mad))
    mask = ndimage.binary_opening(mask, disk(1))
    labels, n = ndimage.label(mask)
    sizes = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    if n == 0:
        raise SystemExit("no change found between the panels")
    mask = np.isin(labels, 1 + np.flatnonzero(sizes >= max(0.0015 * mask.size, 0.05 * sizes.max())))
    radius, sigma, grow = (3, 1.0, 1) if kind == "Recolour" else (7, 3.0, 3)
    pad = radius + 2  # so the frame's edge does not cut the closing short
    mask = ndimage.binary_closing(np.pad(mask, pad), disk(radius))[pad:-pad, pad:-pad]
    if kind != "Recolour":
        mask = ndimage.binary_fill_holes(mask)
    mask = fill_pockets(mask, 0.004)
    mask = ndimage.gaussian_filter(mask.astype(float), sigma) > 0.5
    return ndimage.binary_dilation(mask, disk(grow))


def outline(mask: np.ndarray, tolerance: float = 1.0) -> str:
    """The mask's boundary as an SVG path in pixel units (viewBox 0 0 w h), leaving out stretches that run along
    the frame's edge."""
    h, w = mask.shape
    fmt = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
    on_edge = lambda p, q: (p[0] <= 0 and q[0] <= 0) or (p[0] >= w and q[0] >= w) or (p[1] <= 0 and q[1] <= 0) or (
        p[1] >= h and q[1] >= h)
    parts = []
    for ring in find_contours(np.pad(mask, 1).astype(float), 0.5):
        ring = approximate_polygon(ring - 1, tolerance)
        pts = [(min(max(c + 0.5, 0.0), w), min(max(r + 0.5, 0.0), h)) for r, c in ring]
        segs = list(zip(pts[:-1], pts[1:]))
        keep = [not on_edge(p, q) for p, q in segs]
        if all(keep):
            parts.append("M" + "L".join(f"{fmt(x)} {fmt(y)}" for x, y in pts[:-1]) + "Z")
            continue
        start = keep.index(False) + 1  # walk the ring from just after a dropped stretch
        run: list[tuple[float, float]] = []
        for (p, q), k in zip(segs[start:] + segs[:start], keep[start:] + keep[:start]):
            if k:
                run = run or [p]
                run.append(q)
            elif run:
                parts.append("M" + "L".join(f"{fmt(x)} {fmt(y)}" for x, y in run))
                run = []
        if run:
            parts.append("M" + "L".join(f"{fmt(x)} {fmt(y)}" for x, y in run))
    return "".join(parts)


def knob_height(mask: np.ndarray, split: float) -> float:
    """Where the divider's knob sits, as a share of the height: the first of a few heights near the middle at which
    the knob, opened at `split`, covers none of the change (the lockbox and the car sit at mid-height)."""
    h, w = mask.shape
    cols = mask[:, max(0, round((split - 0.07) * w)):round((split + 0.07) * w) + 1]
    def overlap(y: float) -> float:
        return float(cols[max(0, round((y - 0.08) * h)):round((y + 0.08) * h)].mean())
    candidates = (0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78)
    return next((y for y in candidates if overlap(y) == 0), min(candidates, key=overlap))


# ---------- README -----------------------------------------------------------------------------------------------------

def chat_pass_table() -> dict[int, dict]:
    """Rows such as | #156 "Please use #C8C2B7 color for floors" | applied | 2 | 75 s | $0.15 |."""
    text = README.read_text(encoding="utf-8")
    if "byte-identical in every case" not in text:
        raise SystemExit("README.md no longer says pixels outside every edit were byte-identical")
    row = re.compile(r'^\| #(\d+) "(.+)" \| (\w+) \| (\d+) \| (\d+) s \| \$(\d+\.\d\d) \|$', re.M)
    return {
        int(m[1]): {"text": m[2], "result": m[3], "attempts": int(m[4]), "seconds": int(m[5]), "cost": float(m[6])}
        for m in row.finditer(text)
    }


# ---------- output ---------------------------------------------------------------------------------------------------

def save_webp(img: np.ndarray, name: str) -> int:
    path = OUT / name
    Image.fromarray(img).save(path, "WEBP", quality=QUALITY, method=6)
    return path.stat().st_size


def save_mask(mask: np.ndarray, name: str) -> int:
    """White with the change as alpha: CSS masks read an image's alpha, so a plain greyscale PNG would mask nothing."""
    alpha = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    path = OUT / name
    Image.merge("LA", (Image.new("L", alpha.size, 255), alpha)).save(path, optimize=True)
    return path.stat().st_size


def main() -> None:
    table = chat_pass_table()
    missing = [p.request for p in PAIRS if p.request not in table]
    if missing:
        raise SystemExit(f"README.md's chat-pass table has no row for {missing}")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in [*OUT.glob("*.webp"), *OUT.glob("*.png")]:
        old.unlink()

    composites = {src: np.asarray(Image.open(ROOT / src).convert("RGB")) for src in (EDITOR, REMOVALS)}
    geometry = {src: find_pairs(img) for src, img in composites.items()}
    for src, bands in geometry.items():
        print(f"{src}: {composites[src].shape[1]}x{composites[src].shape[0]}, {len(bands)} pairs")

    pairs = []
    print(f"{'pair':8s} {'request':8s} {'rows':10s} {'before x':10s} {'after x':10s} {'size':8s} "
          f"{'webp KB':>9s} {'mask':>6s} {'changed':>8s} {'noise':>6s}")
    for p in PAIRS:
        (y0, y1), (b0, b1), (a0, a1) = geometry[p.composite][p.band]
        img = composites[p.composite]
        before, after = img[y0:y1, b0:b1], img[y0:y1, a0:a1]
        noise = check_registration(before, after)
        mask = change_mask(before, after, p.kind)
        h, w = mask.shape
        cols = np.flatnonzero(mask.any(axis=0))
        # The divider opens across the middle of the change, so the first view shows it half done.
        split = min(max((cols[0] + cols[-1] + 1) / 2 / w, 0.15), 0.85)
        kb = (save_webp(before, f"{p.name}-before.webp") + save_webp(after, f"{p.name}-after.webp")) / 1024
        mask_kb = save_mask(mask, f"{p.name}-mask.png") / 1024
        row = table[p.request]
        line = (f"{row['result'].capitalize()} in {row['attempts']} attempt{'s' if row['attempts'] > 1 else ''}, "
                f"{row['seconds']} s, ${row['cost']:.2f}; outside the edit: byte-identical")
        print(f"{p.name:8s} #{p.request:<7d} {f'{y0}-{y1}':10s} {f'{b0}-{b1}':10s} {f'{a0}-{a1}':10s} "
              f"{f'{w}x{h}':8s} {kb:9.1f} {mask_kb:6.1f} {mask.mean():8.1%} {noise:6.2f}")
        pairs.append({
            "id": p.name,
            "request": p.request,
            "text": row["text"],
            "kind": p.kind,
            "result": row["result"],
            "attempts": row["attempts"],
            "seconds": row["seconds"],
            "cost": row["cost"],
            "outside": "byte-identical",
            "line": line,
            **({"why": p.why} if p.why else {}),
            "size": {"w": w, "h": h},
            "before": f"{p.name}-before.webp",
            "after": f"{p.name}-after.webp",
            "mask": f"{p.name}-mask.png",
            "share": round(float(mask.mean()), 4),
            "split": round(float(split), 3),
            "knob": knob_height(mask, split),
            "outline": outline(mask),
            "alt": {"before": p.alt_before, "after": p.alt_after},
            "crop": {"composite": p.composite, "rows": [y0, y1], "before": [b0, b1], "after": [a0, a1]},
        })
    data = {
        "source": "README.md, chat pass: saved customer requests from AutoHDR's editor dataset, one view each",
        "pairs": pairs,
    }
    DATA.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(pairs)} pairs to {DATA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
