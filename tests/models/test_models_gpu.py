"""Real Grounding DINO, SAM 2.1 and LaMa on the GPU. Skipped without CUDA or the dataset."""
from __future__ import annotations

import numpy as np
from models_fixtures import gpu, load_case

from shootagent.models import inpaint, segment
from shootagent.models._composite import affected_region

# customer-train-010: one silver car on the driveway, roughly x 0.05-0.31, y 0.27-0.62 of the frame.
CAR_BOX = (0.05, 0.27, 0.31, 0.62)


@gpu
def test_find_car_returns_full_resolution_mask_on_the_car():
    img = load_case("customer-train-010")
    found = segment.find(img, "car")
    assert found, "no car found in a photo with one car"
    car = found[0]
    assert car.mask.shape == img.shape[:2] and car.mask.dtype == bool
    assert segment.box_iou(car.box_norm, CAR_BOX) > 0.8


@gpu
def test_click_with_box_segments_the_whole_car():
    # A lone click on the hood returns just the hood (SAM's scores rank it first); the API's box narrows
    # the ambiguity to the object.
    img = load_case("customer-train-010")
    h, w = img.shape[:2]
    mask = segment.segment_points(img, [(0.18, 0.47)], [1], box_norm=(0.04, 0.25, 0.32, 0.64))
    assert mask.shape == (h, w) and mask[int(0.47 * h), int(0.18 * w)]
    assert segment.box_iou(segment.mask_box_norm(mask), CAR_BOX) > 0.7


@gpu
def test_lama_removes_a_square_from_a_flat_wall():
    img = np.full((600, 800, 3), (200, 196, 188), np.uint8)
    img[250:350, 350:450] = (20, 20, 20)
    mask = np.zeros((600, 800), bool)
    mask[250:350, 350:450] = True
    out = inpaint.lama_inpaint(img, mask)
    region = affected_region(mask, 8, 3)
    assert np.array_equal(out[~region], img[~region])
    assert np.abs(out[mask].astype(int) - (200, 196, 188)).mean() < 8
