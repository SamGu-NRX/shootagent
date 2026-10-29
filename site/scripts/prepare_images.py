"""Prepare the landing page's images from AutoHDR's hackathon data and ShootAgent's own tools.

Run from the repository root, with the `local` extra installed and AUTOHDR_DEFECT_DIR set:

    uv run python site/scripts/prepare_images.py

Two real cases from the defect set, processed by the same tools the agent calls, with no model API:

* defect_train_0016 (the hero). The editor's camera-removal step erased a stop sign. `run_qc_checks` finds it
  missing from the finished photo; `restore_from_brackets` brings it back through the candidate's mask.
* defect_train_0044 (the film). The editor fused ten brackets taken from two camera positions into one photo.
  `run_qc_checks` groups them by feature matching; `regroup_brackets` fuses each group into its own view.

Plus two customer edits from the README's before/after composites: the bedroom walls recoloured to a
reference, and the grey car removed. Output: WebP photos and PNG masks in site/public/img, and
site/src/assets.json with the positions and measurements the page's overlays and captions use.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

from shootagent.config import get_settings
from shootagent.ingest import create_shoot_from_defect_cases
from shootagent.qc.analyze import reference_for_fix
from shootagent.store import ShootStore
from shootagent.tools import ToolContext, run_tool
from shootagent.trace import NullTrace

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "site" / "public" / "img"
ASSETS = ROOT / "site" / "src" / "assets.json"
SCRATCH = Path("/tmp/shootagent-landing-store")


def save_webp(img: np.ndarray | Image.Image, name: str, width: int | None = None, quality: int = 80) -> dict:
    pil = Image.fromarray(img) if isinstance(img, np.ndarray) else img
    pil = pil.convert("RGB")
    if width and pil.width > width:
        pil = pil.resize((width, round(pil.height * width / pil.width)), Image.Resampling.LANCZOS)
    path = OUT / name
    pil.save(path, "WEBP", quality=quality, method=6)
    print(f"  {name:26s} {pil.width}x{pil.height}  {path.stat().st_size // 1024} KB")
    return {"w": pil.width, "h": pil.height}


def save_mask(mask: np.ndarray, name: str, width: int, blur: float = 1.5) -> None:
    h, w = mask.shape
    im = Image.fromarray((mask * 255).astype(np.uint8)).resize((width, round(width * h / w)), Image.Resampling.LANCZOS)
    im.filter(ImageFilter.GaussianBlur(blur)).save(OUT / name, optimize=True)


def norm_box(mask: np.ndarray) -> list[float]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return [round(xs.min() / w, 4), round(ys.min() / h, 4), round((xs.max() + 1) / w, 4), round((ys.max() + 1) / h, 4)]


def ingest(case: str):
    settings = get_settings()
    if not settings.autohdr_defect_dir:
        raise SystemExit("Set AUTOHDR_DEFECT_DIR to the unzipped defect dataset.")
    store = ShootStore(SCRATCH)
    shoot = create_shoot_from_defect_cases(store, case, [Path(settings.autohdr_defect_dir) / case])
    view = shoot.views[0]
    return store, shoot.id, view.id, ToolContext(store, shoot.id, NullTrace(), settings, call_id=f"landing-{case}")


def erased_sign() -> dict:
    store, sid, vid, ctx = ingest("defect_train_0016")
    checked = run_tool(ctx, "run_qc_checks", {"view": vid})
    view = store.load(sid).view(vid)
    cand = next(f for f in view.findings if f.type == "camera_person_removal")
    restored = run_tool(ctx, "restore_from_brackets", {
        "view": vid, "mask": cand.evidence["mask"],
        "intent": "the red STOP sign is back on its pole, as every bracket shows",
    })
    if not restored.ok:
        raise SystemExit(f"restore_from_brackets failed: {restored.summary}")
    view = store.load(sid).view(vid)
    delivered = store.get_image(sid, view.versions[0].blob)
    fixed = store.get_image(sid, view.version(restored.version).blob)
    captured, valid = reference_for_fix(store, sid, vid, long_edge=3072, target_blob=view.versions[0].blob)
    captured = np.where(valid[..., None], captured, delivered)
    region = store.get_mask(sid, view.version(restored.version).mask)
    outside = int(np.abs(fixed.astype(int) - delivered.astype(int)).max(axis=2)[~region].max())
    print(f"  {checked.summary[:90]}")
    print(f"  {restored.summary} Outside the mask: {outside}/255; mask {region.mean():.2%} of the frame.")
    size = save_webp(delivered, "street-delivered.webp", 1440)
    save_webp(captured, "street-captured.webp", 1440)
    save_webp(fixed, "street-fixed.webp", 1440)
    save_mask(region, "street-sign-mask.png", 1440)
    return {
        "street": {
            "size": size,
            "sign": norm_box(region),
            "candidate": [round(x, 4) for x in cand.region],
            "confidence": round(cand.confidence or 0, 2),
            "outside": outside,
            "maskShare": round(float(region.mean()), 4),
        }
    }


def fused_rooms() -> dict:
    store, sid, vid, ctx = ingest("defect_train_0044")
    run_tool(ctx, "run_qc_checks", {"view": vid})
    view = store.load(sid).view(vid)
    cand = next(f for f in view.findings if f.type == "ghosting_grouping")
    groups = cand.evidence.get("groups") or (view.meta.get("qc") or {}).get("groups")
    split = run_tool(ctx, "regroup_brackets", {"view": vid, "groups": groups})
    if not split.ok:
        raise SystemExit(f"regroup_brackets failed: {split.summary}")
    print(f"  {cand.explanation[:120]}")
    print(f"  {split.summary}")
    shoot = store.load(sid)
    delivered = store.get_image(sid, shoot.view(vid).versions[0].blob)
    a, b = (store.get_image(sid, shoot.view(x).head_version.blob) for x in split.data["new_views"])
    h, w = delivered.shape[:2]
    # The film is a 2:1 band; each split view becomes a square panel.
    bh = w // 2
    y0 = (h - bh) // 2
    film = save_webp(delivered[y0:y0 + bh], "laundry-delivered.webp", 2240)
    # View A shares the delivered photo's frame closely enough to show through the viewfinder.
    a_up = np.asarray(Image.fromarray(a).resize((w, h), Image.Resampling.LANCZOS))
    save_webp(a_up[y0:y0 + bh], "laundry-a-band.webp", 2240)
    for name, img in (("laundry-a.webp", a), ("laundry-b.webp", b)):
        ih, iw = img.shape[:2]
        s = min(ih, iw)
        x0 = (iw - s) // 2
        save_webp(img[(ih - s) // 2:(ih - s) // 2 + s, x0:x0 + s], name, 1120)
    return {"laundry": {"film": film, "groups": groups, "explanation": cand.explanation,
                        "confidence": round(cand.confidence or 0, 2)}}


def customer_edits() -> dict:
    walls = Image.open(ROOT / "docs/media/editor-requests-before-after.jpg").convert("RGB")
    before, after = walls.crop((0, 949, 700, 1416)), walls.crop((708, 949, 1408, 1416))
    save_webp(before, "walls-before.webp", quality=84)
    save_webp(after, "walls-after.webp", quality=84)
    # Where the edit changed the photo: the recoloured walls, used for the highlight and the wipe. The change is a
    # hue change, so it is measured on chroma (Lab a, b), which JPEG noise in the bedding and window barely moves;
    # then only large connected regions are kept.
    from skimage.color import rgb2lab

    lab_b, lab_a = rgb2lab(np.asarray(before) / 255.0), rgb2lab(np.asarray(after) / 255.0)
    chroma = np.hypot(lab_a[..., 1] - lab_b[..., 1], lab_a[..., 2] - lab_b[..., 2])
    region = ndimage.binary_opening(chroma > 10, iterations=2)
    labels, n = ndimage.label(region)
    sizes = ndimage.sum(region, labels, range(1, n + 1))
    keep = np.isin(labels, [i + 1 for i, size in enumerate(sizes) if size > 0.01 * region.size])
    keep = ndimage.binary_fill_holes(ndimage.binary_closing(keep, iterations=3))
    Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)).save(
        OUT / "walls-mask.png", optimize=True)
    cars = Image.open(ROOT / "docs/media/removals-before-after.jpg").convert("RGB")
    save_webp(cars.crop((0, 468, 460, 928)), "car-before.webp", quality=84)
    save_webp(cars.crop((468, 468, 928, 928)), "car-after.webp", quality=84)
    print(f"  walls mask covers {keep.mean():.1%} of the frame")
    return {"walls": {"share": round(float(keep.mean()), 4)}}


def main() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    for old in OUT.glob("*"):
        old.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    print("erased stop sign (defect_train_0016):")
    data = erased_sign()
    print("two camera positions in one photo (defect_train_0044):")
    data |= fused_rooms()
    print("customer edits (README composites):")
    data |= customer_edits()
    ASSETS.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {ASSETS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
