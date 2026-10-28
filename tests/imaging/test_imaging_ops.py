"""Pixel operations with one correct answer."""
import numpy as np
import pytest
from synth import expose, scene

from shootagent.imaging import adjust
from shootagent.imaging import composite as C
from shootagent.imaging.color import delta_e, hex_to_lab, rgb_to_lab, srgb_to_linear


@pytest.fixture(scope="module")
def img():
    return expose(scene(1), 0.0)


def disk_mask(h, w, cx, cy, r):
    yy, xx = np.mgrid[:h, :w]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_composite_outside_feather_support_is_byte_identical(img):
    # fails if a feathered composite leaks into pixels beyond the dilated mask
    edited = 255 - img
    m = disk_mask(*img.shape[:2], 400, 300, 80)
    out = C.composite(img, edited, m, feather_px=12)
    support = C.feather_support(m, 12)
    assert np.array_equal(out[~support], img[~support])
    assert np.array_equal(out[m], edited[m])
    d = C.outside_delta(img, out, support)
    assert d["max"] == 0 and d["changed_frac"] == 0.0
    # the edge really is soft: some ring pixels are a blend of both images
    ring = support & ~m
    blended = (out[ring] != img[ring]).any(axis=1) & (out[ring] != edited[ring]).any(axis=1)
    assert blended.any()


def test_masked_exposure_leaves_outside_untouched_and_doubles_linear_light_inside(img):
    # fails if exposure is applied in gamma space or the masked path changes pixels outside the mask
    m = disk_mask(*img.shape[:2], 300, 300, 100)
    out = adjust.exposure(img, 1.0, mask=m, feather_px=0)
    assert np.array_equal(out[~m], img[~m])
    lin_in, lin_out = srgb_to_linear(img)[m], srgb_to_linear(out)[m]
    ok = (lin_in.max(axis=1) < 0.45) & (lin_in.min(axis=1) > 0.02)
    ratio = np.median(lin_out[ok] / lin_in[ok])
    assert ratio == pytest.approx(2.0, rel=0.03)


def test_white_balance_keeps_luminance_and_warms(img):
    # fails if temperature changes overall brightness or goes the wrong direction
    out = adjust.white_balance(img, temp=60)
    lum = lambda x: (srgb_to_linear(x) @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()
    assert lum(out) == pytest.approx(lum(img), rel=0.03)
    assert out[..., 0].mean() > img[..., 0].mean() and out[..., 2].mean() < img[..., 2].mean()


@pytest.mark.parametrize("target", ["#3b6fb6", "#b8473d"])
def test_recolor_hits_target_and_keeps_texture(img, target):
    # fails if recolour misses the requested colour, flattens the surface, or touches pixels outside its feather
    m = np.zeros(img.shape[:2], bool)
    m[200:420, 300:600] = True
    out = adjust.recolor(img, m, target, match_lightness=True, feather_px=3)
    mean_lab = rgb_to_lab(out)[m].mean(axis=0)
    assert float(delta_e(mean_lab, hex_to_lab(target))) < 3.0
    before_L, after_L = rgb_to_lab(img)[m][:, 0], rgb_to_lab(out)[m][:, 0]
    assert np.corrcoef(before_L, after_L)[0, 1] > 0.85  # shading and texture survive (gamut clipping costs a little)
    support = C.feather_support(m, 3)
    assert np.array_equal(out[~support], img[~support])


def test_recolor_without_lightness_match_preserves_L(img):
    m = np.zeros(img.shape[:2], bool)
    m[100:300, 100:400] = True
    out = adjust.recolor(img, m, "#2f7d4a", match_lightness=False, feather_px=0)
    dL = np.abs(rgb_to_lab(out)[m][:, 0] - rgb_to_lab(img)[m][:, 0])
    assert np.median(dL) < 2.0


def test_sample_bracket_hex_is_exposure_invariant():
    # fails if bracket sampling does not normalise exposure (a dark and a bright bracket must agree)
    lin = np.zeros((64, 64, 3), np.float32)
    lin[:] = [0.05, 0.12, 0.35]  # a blue wall
    evs = [-2.0, 0.0, 2.0]
    br = [expose(lin, ev) for ev in evs]
    m = np.ones((64, 64), bool)
    got = adjust.sample_bracket_hex(br, evs, m)
    mid = adjust.sample_hex(br[1], m)
    assert float(delta_e(hex_to_lab(got), hex_to_lab(mid))) < 2.0


def test_restore_from_reference_brings_back_the_object_in_the_photos_style():
    # fails if restore leaves the erased object missing, ignores the photo's grade, or edits outside its feather
    reference = expose(scene(3), 0.0)
    m = np.zeros(reference.shape[:2], bool)
    m[250:350, 400:520] = True
    look = lambda x: np.clip(x.astype(np.float32) * 1.1 + 8, 0, 255).astype(np.uint8)
    finished = reference.copy()
    finished[m] = finished[m].mean(axis=0).astype(np.uint8)  # object erased to a flat patch
    finished = look(finished)
    out, info = C.restore_from_reference(finished, reference, m, feather_px=4)
    support = C.feather_support(m, 4)
    assert np.array_equal(out[~support], finished[~support])
    err = np.abs(out[m].astype(np.float32) - look(reference)[m].astype(np.float32)).mean()
    assert err < 4.0, err
    assert info["seam_after"] <= info["seam_before"] + 0.5


def test_restore_refuses_mask_outside_bracket_coverage():
    ref = expose(scene(4), 0)
    m = np.zeros(ref.shape[:2], bool)
    m[:100, :100] = True
    valid = np.ones_like(m)
    valid[:120, :120] = False
    with pytest.raises(ValueError, match="coverage"):
        C.restore_from_reference(ref, ref, m, valid=valid)
