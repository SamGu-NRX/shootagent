"""Prepare the landing page's images from AutoHDR's hackathon data and ShootAgent's own tools.

Run from the repository root, with the `local` extra installed and AUTOHDR_DEFECT_DIR set:

    uv run python site/scripts/prepare_images.py

Two real cases from the defect set, processed by the same tools the agent calls, with no model API:

* defect_train_0016 (the hero). The editor's camera-removal step erased a stop sign. `run_qc_checks` finds it
  missing from the finished photo; `grow_mask` widens the candidate's mask to the whole sign, and
  `restore_from_brackets` brings it back through that mask.
* defect_train_0044 (the film). The editor fused ten brackets taken from two camera positions into one photo.
  `run_qc_checks` groups them by feature matching; `regroup_brackets` fuses each group into its own view.

Plus two customer edits from the README's before/after composites: the bedroom walls recoloured to a
reference, and the grey car removed. Output: WebP photos and PNG masks (white, the mask in the alpha
channel, for CSS mask-image) in site/public/img, and site/src/assets.json with the positions and
measurements the page's overlays and captions use. The two labelled copies on the page carry QR codes, as the
listing kit's do, and these point at the unaltered originals the page serves. Only the files in OUTPUTS are
replaced; the folder also holds files this script does not own (edits/).
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
# Every file this script writes to OUT, and so the only ones it deletes before a run.
OUTPUTS = (
    "street-delivered.webp", "street-captured.webp", "street-fixed.webp", "street-sign-mask.png",
    "laundry-delivered.webp", "laundry-a-band.webp", "laundry-a.webp", "laundry-b.webp",
    "walls-before.webp", "walls-after.webp", "walls-mask.png", "car-before.webp", "car-after.webp",
    "qr-walls.svg", "qr-car.svg",
)
SITE = "https://shootagent-ai.vercel.app"
# The QC candidate's mask follows the blue siding and stops where the editor's fill painted the white porch
# roof over the sign's lower-right third, so a restore through it alone reads "STO" with the roof line across
# the corner. As the agent does when a mask misses part of its object, grow it first (grow_mask counts pixels
# on a 1024-px copy). 18 (75 px on this 4240-px photo) is the smallest growth that puts the whole sign, red
# face, white rim and dark edge, inside the mask; 16 covers the face but leaves the edge's lower-right corner
# in the feather.
SIGN_GROW = 18


def save_webp(img: np.ndarray | Image.Image, name: str, width: int | None = None, quality: int = 80) -> dict:
    assert name in OUTPUTS, f"add {name} to OUTPUTS"
    pil = Image.fromarray(img) if isinstance(img, np.ndarray) else img
    pil = pil.convert("RGB")
    if width and pil.width > width:
        pil = pil.resize((width, round(pil.height * width / pil.width)), Image.Resampling.LANCZOS)
    path = OUT / name
    pil.save(path, "WEBP", quality=quality, method=6)
    print(f"  {name:26s} {pil.width}x{pil.height}  {path.stat().st_size // 1024} KB")
    return {"w": pil.width, "h": pil.height}


def save_mask(mask: np.ndarray, name: str, width: int | None = None, blur: float = 1.5) -> None:
    """White PNG with the (softened) mask in its alpha channel: CSS mask-image reads alpha by default."""
    assert name in OUTPUTS, f"add {name} to OUTPUTS"
    h, w = mask.shape
    alpha = Image.fromarray((mask * 255).astype(np.uint8))
    if width:
        alpha = alpha.resize((width, round(width * h / w)), Image.Resampling.LANCZOS)
    im = Image.new("RGBA", alpha.size, (255, 255, 255, 0))
    im.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)))
    im.save(OUT / name, optimize=True)


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
    grown = run_tool(ctx, "grow_mask", {"view": vid, "mask": cand.evidence["mask"], "pixels": SIGN_GROW})
    if not grown.ok:
        raise SystemExit(f"grow_mask failed: {grown.summary}")
    restored = run_tool(ctx, "restore_from_brackets", {
        "view": vid, "mask": grown.data["mask"],
        "intent": "the red STOP sign is back on its pole, as every bracket shows",
    })
    if not restored.ok:
        raise SystemExit(f"restore_from_brackets failed: {restored.summary}")
    view = store.load(sid).view(vid)
    delivered = store.get_image(sid, view.versions[0].blob)
    fixed = store.get_image(sid, view.version(restored.version).blob)
    captured, valid = reference_for_fix(store, sid, vid, long_edge=3072, target_blob=view.versions[0].blob)
    captured = np.where(valid[..., None], captured, delivered)
    # The restore's own mask: the grown mask plus its feather, i.e. every pixel it may change. The mask PNG,
    # the sign box, maskShare and the outside-the-mask delta all use it.
    region = store.get_mask(sid, view.version(restored.version).mask)
    outside = int(np.abs(fixed.astype(int) - delivered.astype(int)).max(axis=2)[~region].max())
    print(f"  {checked.summary[:90]}")
    print(f"  {grown.summary}")
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
    save_mask(keep, "walls-mask.png", blur=1.2)
    cars = Image.open(ROOT / "docs/media/removals-before-after.jpg").convert("RGB")
    save_webp(cars.crop((0, 468, 460, 928)), "car-before.webp", quality=84)
    save_webp(cars.crop((468, 468, 928, 928)), "car-after.webp", quality=84)
    print(f"  walls mask covers {keep.mean():.1%} of the frame")
    return {"walls": {"share": round(float(keep.mean()), 4)}}


def label_codes() -> None:
    """The QR codes on the hero's and the kit's labelled copies, each to the unaltered original it describes."""
    import segno

    for name, original in (("qr-walls.svg", "walls-before.webp"), ("qr-car.svg", "car-before.webp")):
        assert name in OUTPUTS, f"add {name} to OUTPUTS"
        segno.make(f"{SITE}/img/{original}", error="m").save(
            OUT / name, kind="svg", border=2, dark="#111", light="#fff", omitsize=True, xmldecl=False)
        # Scales with the label (viewBox, no fixed size) and keeps its modules sharp when it does.
        svg = (OUT / name).read_text().replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)
        (OUT / name).write_text(svg)
        print(f"  {name:26s} -> {SITE}/img/{original}")


def main() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        (OUT / name).unlink(missing_ok=True)
    print("erased stop sign (defect_train_0016):")
    data = erased_sign()
    print("two camera positions in one photo (defect_train_0044):")
    data |= fused_rooms()
    print("customer edits (README composites):")
    data |= customer_edits()
    label_codes()
    ASSETS.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {ASSETS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
