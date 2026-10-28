"""Build the small images the web UI's mock API serves from web/public/mock/.

Reads a handful of AutoHDR defect cases, writes downscaled copies (max 1600 px long edge), 360 px
thumbnails, and a few synthetic "edited" versions so the before/after slider shows a visible change
in mock mode. The synthetic edits are crude stand-ins for the real tools, not their output.

Run from the repo root with the backend venv (needs Pillow and numpy):
    .venv/Scripts/python web/scripts/make_mock_assets.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

DATASET = Path.home() / "datasets" / "autohdr" / "defect"
OUT = Path(__file__).resolve().parents[1] / "public" / "mock"
LONG_EDGE = 1600
THUMB = 360
BRACKET_EDGE = 1000

CASES = ["0001", "0030", "0015", "0013", "0004", "0039", "0018"]


def fit(im: Image.Image, edge: int) -> Image.Image:
    im = im.convert("RGB")
    scale = edge / max(im.size)
    if scale >= 1:
        return im.copy()
    return im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)


def save(im: Image.Image, name: str, quality: int = 86) -> None:
    im.save(OUT / name, "JPEG", quality=quality, optimize=True)
    fit(im, THUMB).save(OUT / name.replace(".jpg", "_w360.jpg"), "JPEG", quality=80)


def ellipse_mask(size: tuple[int, int], box: list[float], pad: float = 0.0, feather: int = 6) -> Image.Image:
    w, h = size
    x0, y0, x1, y1 = box
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse(
        [(x0 - px) * w, (y0 - py) * h, (x1 + px) * w, (y1 + py) * h], fill=255
    )
    return m.filter(ImageFilter.GaussianBlur(feather)) if feather else m


def rect_mask(size: tuple[int, int], box: list[float], feather: int = 4) -> Image.Image:
    w, h = size
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rectangle([box[0] * w, box[1] * h, box[2] * w, box[3] * h], fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather)) if feather else m


def match_mean(src: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Scale src per channel so its mean matches target (a crude exposure-normalised bracket)."""
    gain = (target.reshape(-1, 3).mean(0) + 1) / (src.reshape(-1, 3).mean(0) + 1)
    return np.clip(src * gain, 0, 255)


def composite(base: Image.Image, top: np.ndarray, mask: Image.Image) -> Image.Image:
    a = np.asarray(mask, dtype=np.float32)[..., None] / 255
    out = np.asarray(base, dtype=np.float32) * (1 - a) + top * a
    return Image.fromarray(out.astype(np.uint8))


def recolor_blue(base: Image.Image, mask: Image.Image) -> Image.Image:
    hsv = np.asarray(base.convert("HSV"), dtype=np.float32)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    target = hsv.copy()
    target[..., 0] = 148  # ~208 degrees on PIL's 0..255 hue scale: a muted slate blue
    target[..., 1] = np.clip(s + 70, 0, 150)
    target[..., 2] = v * 0.97
    rgb = np.asarray(Image.fromarray(target.astype(np.uint8), "HSV").convert("RGB"), dtype=np.float32)
    return composite(base, rgb, mask)


def brighten(base: Image.Image, stops: float) -> Image.Image:
    arr = np.asarray(base, dtype=np.float32) / 255
    lin = arr ** 2.2 * (2 ** stops)
    out = np.clip(lin, 0, 1) ** (1 / 2.2)
    return Image.fromarray((out * 255).astype(np.uint8))


def main() -> int:
    if not DATASET.exists():
        print(f"dataset not found at {DATASET}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    labels = json.loads((DATASET / "labels.json").read_text(encoding="utf-8"))["cases"]
    manifest: dict[str, dict] = {}

    for case in CASES:
        cid = f"defect_train_{case}"
        folder = DATASET / cid
        finished = fit(Image.open(folder / "edited.jpg"), LONG_EDGE)
        save(finished, f"{case}_finished.jpg")
        brackets = sorted(folder.glob("bracket_*.jpg"))
        means = []
        for i, b in enumerate(brackets):
            im = fit(Image.open(b), BRACKET_EDGE)
            save(im, f"{case}_bracket{i}.jpg", quality=72)
            means.append(float(np.asarray(im, dtype=np.float32).mean()))
        mid = len(brackets) // 2
        ref_src = np.asarray(fit(Image.open(brackets[mid]), LONG_EDGE).resize(finished.size), dtype=np.float32)
        reference = Image.fromarray(match_mean(ref_src, np.asarray(finished, dtype=np.float32)).astype(np.uint8))
        save(reference, f"{case}_reference.jpg")
        label = labels.get(cid, {})
        regions = label.get("defect_regions") or []
        manifest[case] = {
            "case": cid,
            "size": list(finished.size),
            "brackets": len(brackets),
            "ev": [round(float(np.log2((m + 1) / (means[mid] + 1))), 2) for m in means],
            "title": label.get("title"),
            "family": label.get("confirmed_defect_family"),
            "box": regions[0]["box_xyxy_normalized"] if regions else None,
            "description": regions[0]["description"] if regions else None,
        }

        ref_arr = np.asarray(reference, dtype=np.float32)
        box = manifest[case]["box"]
        if case == "0001":  # lamp ghost: a loose first attempt that spills, then a tight one
            loose = rect_mask(finished.size, [box[0] - 0.08, box[1] - 0.1, box[2] + 0.1, box[3] + 0.12], 2)
            composite(finished, ref_arr, loose).save(OUT / "0001_fix_try1.jpg", quality=86)
            loose.save(OUT / "0001_mask_try1.png")
            tight = ellipse_mask(finished.size, box, pad=0.05)
            fixed = composite(finished, ref_arr, tight)
            save(fixed, "0001_fix.jpg")
            tight.save(OUT / "0001_mask.png")
        if case == "0030":  # kitchen wall paint back to blue, then a brighter pass from chat
            m = rect_mask(finished.size, box, 3)
            m.save(OUT / "0030_mask.png")
            blue = recolor_blue(finished, m)
            save(blue, "0030_fix.jpg")
            save(brighten(blue, 0.4), "0030_bright.jpg")
            save(brighten(finished, 0.4), "0030_bright_only.jpg")
        if case == "0015":  # floor lamp legs restored from the brackets
            m = ellipse_mask(finished.size, box, pad=0.02)
            m.save(OUT / "0015_mask.png")
            save(composite(finished, ref_arr, m), "0015_fix.jpg")
        if case == "0004":  # hand in the sky: two fills that leave visible smears (both fail their check)
            w, h = finished.size
            x0, y0, x1, y1 = (round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h))
            m = ellipse_mask(finished.size, box, pad=0.04)
            m.save(OUT / "0004_mask.png")
            smear = finished.filter(ImageFilter.GaussianBlur(28))
            save(composite(finished, np.asarray(smear, dtype=np.float32), m), "0004_try1.jpg")
            flat = np.asarray(finished, dtype=np.float32).copy()
            sky = flat[y1 : min(h, y1 + 60), x0:x1].reshape(-1, 3).mean(0)
            flat[:] = sky
            save(composite(finished, flat, m), "0004_try2.jpg")
        if case == "0013":
            m = rect_mask(finished.size, box, 3)
            m.save(OUT / "0013_mask.png")
            save(recolor_blue(finished, m), "0013_fix.jpg")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"wrote {len(list(OUT.iterdir()))} files, {total / 1e6:.1f} MB to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
