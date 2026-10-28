"""Difference regions, screen placement, overlays and image loading."""
import cv2
import numpy as np
import pytest
from PIL import Image
from synth import expose, grade, scene

from shootagent.imaging import diffmap, overlay, screen
from shootagent.imaging.composite import feather_support
from shootagent.imaging.io import load_rgb


def covered(bbox, box):
    """Fraction of `box` covered by `bbox`."""
    x0, y0 = max(bbox[0], box[0]), max(bbox[1], box[1])
    x1, y1 = min(bbox[2], box[2]), min(bbox[3], box[3])
    return max(0, x1 - x0) * max(0, y1 - y0) / ((box[2] - box[0]) * (box[3] - box[1]))


@pytest.fixture(scope="module")
def ref():
    return expose(scene(20, 1200, 800), 0)


def test_globally_graded_finished_has_no_regions(ref):
    # fails if an ordinary editor grade (brighter, warmer) is reported as a defect: clean photos must stay clean
    res = diffmap.diff(grade(ref), ref, np.ones(ref.shape[:2], bool))
    assert res.regions == []


def test_erased_object_is_missing_and_pasted_fire_is_added(ref):
    # fails if structure removed from, or flames added to, the finished photo are not located
    ref2 = ref.copy()
    ref2[600:720, 100:260] = 30  # dark empty firebox in the brackets
    fin = grade(ref2)
    fin[300:420, 500:640] = np.median(fin[280:300, 500:640].reshape(-1, 3), axis=0).astype(np.uint8)
    rng = np.random.default_rng(0)
    for _ in range(40):
        c = (int(rng.integers(115, 245)), int(rng.integers(615, 705)))
        cv2.circle(fin, c, int(rng.integers(4, 14)), (255, int(rng.integers(120, 200)), 30), -1)
    res = diffmap.diff(fin, ref2, np.ones(ref.shape[:2], bool))
    missing = [r for r in res.regions if r.kind == "missing"]
    added = [r for r in res.regions if r.kind == "added"]
    assert any(covered(r.bbox, [500 / 1200, 300 / 800, 640 / 1200, 420 / 800]) > 0.5 for r in missing)
    fire = [r for r in added if covered(r.bbox, [100 / 1200, 600 / 800, 260 / 1200, 720 / 800]) > 0.5]
    assert fire and fire[0].stats["fire_frac"] > 0.05


def test_neutralised_blue_wall_is_a_chroma_region_reporting_the_bracket_blue():
    # fails if a blue wall rendered grey is not flagged, or the reported bracket colour is not blue
    h, w = 600, 900
    lin = scene(21, w, h)
    tex = 1 + 0.05 * np.random.default_rng(1).normal(size=(300, 400, 1)).astype(np.float32)
    lin[100:400, 450:850] = np.array([0.10, 0.16, 0.42], np.float32) * tex
    ref = expose(lin, 0)
    fin = grade(ref)
    fin[100:400, 450:850] = fin[100:400, 450:850].mean(axis=2, keepdims=True).astype(np.uint8)
    res = diffmap.diff(fin, ref, np.ones((h, w), bool))
    chroma = [r for r in res.regions if r.kind == "chroma"]
    assert chroma and covered(chroma[0].bbox, [0.5, 1 / 6, 850 / 900, 4 / 6]) > 0.5
    r, g, b = (int(chroma[0].stats["reference_hex"][i:i + 2], 16) for i in (1, 3, 5))
    assert b > r + 20


def test_quad_from_mask_recovers_corners():
    m = np.zeros((480, 640), np.uint8)
    q = np.array([[100, 80], [520, 110], [500, 400], [120, 380]], np.int32)
    cv2.fillConvexPoly(m, q, 1)
    assert np.abs(screen.quad_from_mask(m > 0) - q).max() <= 2.5


def test_place_on_screen_fills_quad_in_order_and_leaves_rest():
    # fails if content is mirrored/rotated on the quad or pixels outside the screen change
    img = np.full((480, 640, 3), 90, np.uint8)
    m = np.zeros((480, 640), np.uint8)
    cv2.fillConvexPoly(m, np.array([[100, 80], [520, 110], [500, 400], [120, 380]]), 1)
    m = m > 0
    content = np.zeros((300, 500, 3), np.uint8)
    content[:, :250] = (255, 0, 0)
    content[:, 250:] = (0, 0, 255)
    out = screen.place_on_screen(img, m, content, sheen=0.0)
    outside = ~feather_support(m, 1)
    assert np.array_equal(out[outside], img[outside])
    assert out[240, 200, 0] > 200 and out[240, 420, 2] > 200


def test_watermark_auto_contrast_and_position():
    # fails if auto colour puts white text on a white wall, or the mark lands in the wrong corner
    light = np.full((600, 900, 3), 235, np.uint8)
    out = overlay.watermark(light, "Harbor Realty", position="bottom-right")
    changed = np.any(out != light, axis=2)
    ys, xs = np.nonzero(changed)
    assert ys.min() > 300 and xs.min() > 450
    assert out[changed].mean() < 200
    dark = np.full((600, 900, 3), 30, np.uint8)
    out2 = overlay.watermark(dark, "Harbor Realty", position="top-left")
    ch2 = np.any(out2 != dark, axis=2)
    assert np.nonzero(ch2)[0].max() < 300 and out2[ch2].max() > 200


def test_lot_lines_pass_through_points_and_reject_bad_input():
    img = np.full((500, 800, 3), 60, np.uint8)
    pts = [[0.2, 0.2], [0.8, 0.25], [0.7, 0.8], [0.25, 0.75]]
    out = overlay.lot_lines(img, pts, color="#ffd23f", label="Lot 12")
    for x, y in pts:
        px = out[int(y * 500), int(x * 800)]
        assert px[0] > 200 and px[1] > 160
    with pytest.raises(ValueError):
        overlay.lot_lines(img, [[0.1, 0.1], [0.5, 0.5]], closed=True)
    with pytest.raises(ValueError):
        overlay.lot_lines(img, [[10, 10], [200, 50], [30, 300]])  # pixels, not normalised


def test_load_rgb_applies_exif_orientation(tmp_path):
    # fails if a portrait phone JPEG (orientation 6) loads sideways
    arr = np.zeros((40, 80, 3), np.uint8)
    arr[:, :40] = 255
    im = Image.fromarray(arr)
    exif = im.getexif()
    exif[274] = 6
    p = tmp_path / "o6.jpg"
    im.save(p, exif=exif.tobytes(), quality=95)
    out = load_rgb(p)
    assert out.shape == (80, 40, 3)
    assert out[:40].mean() > 200 and out[40:].mean() < 60
