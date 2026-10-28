"""The outside-mask guarantee for every edit path, with the models replaced by deterministic fakes.

The fakes return a fill that differs from the input everywhere, so any crop-offset, resize or feather
bug shows up as a non-zero delta outside the region the function promises to confine itself to.
"""
from __future__ import annotations

import base64
import io

import cv2
import numpy as np
import pytest
from PIL import Image

from shootagent.models import genedit, inpaint
from shootagent.models._composite import affected_region, composite, grow


def blobs(h: int, w: int, rects) -> np.ndarray:
    m = np.zeros((h, w), bool)
    for y0, y1, x0, x1 in rects:
        m[y0:y1, x0:x1] = True
    return m


@pytest.mark.parametrize("feather", [0, 7])
def test_composite_outside_grown_mask_is_byte_identical_and_inside_is_edited(rng, feather):
    base = rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)
    edited = rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)
    mask = blobs(120, 160, [(30, 60, 40, 90), (0, 10, 150, 160)])  # one blob touches the border
    out = composite(base, edited, mask, feather)
    outside = ~grow(mask, feather)
    assert np.array_equal(out[outside], base[outside])
    assert np.array_equal(out[mask], edited[mask])
    if feather:
        band = grow(mask, feather) & ~mask
        assert (out[band] != base[band]).any()  # the feather actually blends


def test_lama_inpaint_fills_every_component_and_leaves_the_rest(monkeypatch, rng):
    # A fake LaMa that returns mid-grey: every hole pixel must become grey, nothing else may change.
    import torch

    monkeypatch.setattr(inpaint._LAMA, "get", lambda: (lambda img, mask: torch.full_like(img, 0.5)))
    img = rng.integers(0, 256, (300, 400, 3), dtype=np.uint8)
    mask = blobs(300, 400, [(20, 40, 30, 70), (250, 300, 350, 400), (140, 150, 10, 390)])
    out = inpaint.lama_inpaint(img, mask, dilate_px=4, feather_px=2)
    assert out.shape == img.shape and out.dtype == np.uint8
    region = affected_region(mask, 4, 2)
    assert np.array_equal(out[~region], img[~region])
    assert np.all(np.abs(out[grow(mask, 4)].astype(int) - 128) <= 1)


def test_lama_inpaint_rejects_empty_and_mismatched_masks(rng):
    img = rng.integers(0, 256, (50, 60, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="empty mask"):
        inpaint.lama_inpaint(img, np.zeros((50, 60), bool))
    with pytest.raises(ValueError, match="does not match"):
        inpaint.lama_inpaint(img, np.ones((60, 50), bool))


def fake_endpoint(tint=(0.9, 20.0), draw=(255, 0, 0)):
    """Stands in for images.edit: re-renders the whole sent crop with a global tint (the drift the real
    model adds) and paints the editable area a solid colour."""
    calls = []

    def call(model, images, mask, prompt, size, quality):
        calls.append({"model": model, "n_images": len(images), "size": size, "prompt": prompt})
        sent = np.asarray(Image.open(io.BytesIO(images[0])).convert("RGB")).astype(np.float32)
        alpha = np.asarray(Image.open(io.BytesIO(mask)))[..., 3]
        out = np.clip(sent * tint[0] + tint[1], 0, 255).astype(np.uint8)
        out[alpha == 0] = draw
        buf = io.BytesIO()
        Image.fromarray(out).save(buf, "PNG")
        usage = {"input_tokens": 1564, "input_tokens_details": {"image_tokens": 1536, "text_tokens": 28}, "output_tokens": 343}
        return {"data": [{"b64_json": base64.b64encode(buf.getvalue()).decode()}], "usage": usage}

    return call, calls


def smooth_photo(h: int, w: int) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.stack([60 + 120 * x / w, 80 + 100 * y / h, 140 + 0 * x], axis=2).astype(np.uint8)


def test_generative_edit_confines_changes_and_cancels_tint(monkeypatch):
    call, calls = fake_endpoint()
    monkeypatch.setattr(genedit, "_call_edit", call)
    img = smooth_photo(900, 1500)
    mask = blobs(900, 1500, [(300, 500, 600, 800)])
    r = genedit.generative_edit(img, mask, "turn on the lamp", model="gpt-image-2", feather_px=5)

    assert r.image.shape == img.shape
    assert r.mask[mask].all()
    assert np.array_equal(r.image[~grow(r.mask, 5)], img[~grow(r.mask, 5)])
    x0, y0, x1, y1 = r.crop_box
    assert 0 <= x0 < 600 and 800 < x1 <= 1500 and 0 <= y0 < 300 and 500 < y1 <= 900
    # The painted fill arrives, and the fake's global tint (x0.9 + 20) is inverted by the colour fit.
    centre = r.image[400, 700].astype(int)
    assert centre[0] > 200 and centre[1] < 60 and centre[2] < 60
    for c, (gain, offset) in enumerate(r.color_fit):
        if c == 2:
            continue  # the blue channel is constant in this photo, so its gain is unconstrained
        assert gain == pytest.approx(1 / 0.9, abs=0.02) and offset == pytest.approx(-20 / 0.9, abs=2.0)
    assert calls[0]["size"] in genedit.SIZES and calls[0]["n_images"] == 1
    assert r.usd == pytest.approx((28 * 5 + 1536 * 8 + 343 * 30) / 1e6)


def test_generative_edit_sends_references_after_the_crop_and_names_them(monkeypatch):
    call, calls = fake_endpoint()
    monkeypatch.setattr(genedit, "_call_edit", call)
    img = smooth_photo(600, 900)
    mask = blobs(600, 900, [(200, 300, 300, 450)])
    swatch = np.full((64, 64, 3), (163, 177, 194), np.uint8)
    genedit.generative_edit(img, mask, "repaint the wall", references=[("colour swatch #A3B1C2", swatch)], model="gpt-image-2")
    assert calls[0]["n_images"] == 2
    assert "image 2: colour swatch #A3B1C2" in calls[0]["prompt"]


def test_generative_edit_refuses_unpriced_model_before_calling(monkeypatch):
    monkeypatch.setattr(genedit, "_call_edit", lambda *a, **k: pytest.fail("endpoint called"))
    img = smooth_photo(100, 100)
    with pytest.raises(KeyError, match="no price"):
        genedit.generative_edit(img, blobs(100, 100, [(10, 20, 10, 20)]), "x", model="dall-e-9")


def test_mask_png_marks_editable_pixels_transparent():
    editable = blobs(40, 60, [(5, 15, 10, 30)])
    alpha = np.asarray(Image.open(io.BytesIO(genedit.mask_png(editable))))[..., 3]
    assert alpha.shape == (40, 60)
    assert np.all(alpha[editable] == 0) and np.all(alpha[~editable] == 255)


@pytest.mark.parametrize(
    ("w", "h", "size"), [(3000, 2000, (1536, 1024)), (2000, 3000, (1024, 1536)), (1000, 1100, (1024, 1024))]
)
def test_pick_size_matches_aspect(w, h, size):
    assert genedit.pick_size(w, h) == size


def test_choose_crop_contains_mask_inside_frame_with_target_aspect():
    mask = blobs(4000, 6000, [(3700, 3990, 5600, 5990)])  # bottom-right corner object
    (x0, y0, x1, y1), size = genedit.choose_crop(mask)
    assert 0 <= x0 <= 5600 and 5990 <= x1 <= 6000 and 0 <= y0 <= 3700 and 3990 <= y1 <= 4000
    assert abs((x1 - x0) / (y1 - y0) - size[0] / size[1]) < 0.01


def test_fit_color_recovers_known_gain_and_offset(rng):
    ref = rng.integers(20, 220, (64, 64, 3)).astype(np.uint8)
    src = np.clip((ref.astype(np.float32) - 12) / 1.1, 0, 255).round().astype(np.uint8)  # ref = 1.1*src + 12
    out, fit = genedit.fit_color(src, ref, np.ones((64, 64), bool))
    for gain, offset in fit:
        assert gain == pytest.approx(1.1, abs=0.01) and offset == pytest.approx(12, abs=1.0)
    assert np.abs(out.astype(int) - ref).max() <= 1


def test_match_detail_adds_only_the_photos_noise_not_its_edges(rng):
    # A smooth wall with sensor noise (std 1.5) next to hard black/white stripes. A plain std of the
    # high-pass counted the stripes as noise and painted a grainy patch (shoot 6de3da631b).
    h, w = 400, 400
    orig = np.full((h, w, 3), 180.0, np.float32)
    orig[:, :120] = np.where((np.arange(120) // 6) % 2 == 0, 20.0, 240.0)[None, :, None]
    orig += rng.normal(0, 1.5, (h, w))[..., None]
    orig = np.clip(orig, 0, 255).round().astype(np.uint8)
    mask = np.zeros((h, w), bool)
    mask[150:250, 220:320] = True
    gen = cv2.GaussianBlur(orig, (0, 0), 1.2)  # the model's re-render is softer everywhere
    gen[mask] = 180  # and its fill is perfectly clean
    out = genedit.match_detail(gen, orig, mask)
    inside = out[170:230, 240:300, 1].astype(np.float32)
    assert 1.0 < inside.std() < 2.2


def test_lama_then_generative_removal_is_confined_to_its_region(monkeypatch, rng):
    import torch

    from shootagent.models import remove

    monkeypatch.setattr(inpaint._LAMA, "get", lambda: (lambda img, mask: torch.full_like(img, 0.5)))
    call, calls = fake_endpoint()
    monkeypatch.setattr(genedit, "_call_edit", call)
    img = rng.integers(0, 256, (600, 900, 3), dtype=np.uint8)
    mask = blobs(600, 900, [(200, 420, 300, 450)])
    r = remove.remove_object(img, mask, what="trash bin", method="lama+generative", model="gpt-image-2")
    assert r.method == "lama+generative" and r.region[mask].all()
    assert np.array_equal(r.image[~r.region], img[~r.region])
    assert "Remove the trash bin completely" in calls[0]["prompt"]
