"""Checks for the parts of the film tools that have one right answer.

Run with: .venv/Scripts/python -m pytest experiments/film/tests
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_clip as cc  # noqa: E402
import parallax as px  # noqa: E402


def test_merge_collinear_joins_an_edge_cut_by_a_crossing_and_keeps_parallel_edges_apart():
    # A vertical mullion cut in two by a meeting rail, plus the other side of the mullion 6 px away.
    segs = np.array([[100, 50, 100, 200], [100.5, 230, 100.5, 400], [106, 50, 106, 400]], np.float32)
    merged = cc.merge_collinear(segs)
    assert len(merged) == 2
    lengths = sorted(np.hypot(merged[:, 2] - merged[:, 0], merged[:, 3] - merged[:, 1]))
    assert lengths == pytest.approx([350, 350], abs=1.0)


def _scene(h=360, w=640, seed=0):
    """Flat 'walls' and 'sky' with a few textured objects, the mix that made dense flow drift."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 170, np.float32)
    img[: h // 3] = 215
    img[:, w // 2:] -= 25
    for _ in range(14):
        y, x = rng.integers(20, h - 60), rng.integers(20, w - 80)
        img[y:y + 40, x:x + 60] = rng.uniform(40, 120) + rng.normal(0, 25, (40, 60))
    return np.clip(img, 0, 255).astype(np.uint8)


def _push_in(h=360, w=640, steps=13):
    """Frames of a 12% push-in over 3 s at 0.25 s steps, and each frame's frame-px -> source-px matrix."""
    src = _scene(720, 1280)
    frames, to_src = [], []
    for k in range(steps):
        s = 2 / (1 + 0.12 * k / (steps - 1))  # source px per frame px
        B = np.array([[s, 0, 640 - s * w / 2], [0, s, 360 - s * h / 2], [0, 0, 1]])
        frames.append(cv2.warpAffine(src, B[:2], (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP))
        to_src.append(B)
    return frames, to_src


@pytest.fixture(scope="module")
def push_in_chain():
    frames, to_src = _push_in()
    return cc.Chain(frames), to_src


def test_chain_maps_track_a_known_push_in_to_a_pixel(push_in_chain):
    """Fails if the frame-k -> frame-0 map drifts (chained dense flow alone was off by >9 px at p95)."""
    chain, to_src = push_in_chain
    h, w = chain.shape
    k = len(to_src) - 1
    T = np.linalg.inv(to_src[0]) @ to_src[k]  # frame k -> frame 0
    ys, xs = np.mgrid[0:h, 0:w]
    gt = np.stack([T[0, 0] * xs + T[0, 2], T[1, 1] * ys + T[1, 2]], -1)
    err = np.linalg.norm(chain.maps[k] - gt, axis=-1)[10:-10, 10:-10]
    assert np.percentile(err, 95) < 1.5


def test_forward_flags_points_that_left_the_frame(push_in_chain):
    """Fails if frame-0 points pushed out of view are reported as tracked (the source of line boxes far
    outside the frame on real clips) or if points still in view are dropped."""
    chain, to_src = push_in_chain
    h, w = chain.shape
    k = len(to_src) - 1
    pts0 = np.array([[w / 2, h / 2], [w * 0.4, h * 0.6], [3, 3], [w - 4, h / 2]], np.float32)
    x, ok = chain.forward(k, pts0)
    T = np.linalg.inv(to_src[k]) @ to_src[0]  # frame 0 -> frame k
    truth = pts0 * T[0, 0] + T[:2, 2]
    assert ok.tolist() == [True, True, False, False]
    assert np.abs(x[:2] - truth[:2]).max() < 1.0


def test_truck_disocclusion_matches_the_gap_a_near_block_opens(monkeypatch):
    """A near block on a far wall, camera moved sideways: the uncovered strip is the parallax wide."""
    h, w = 400, 300
    rgb = torch.full((h, w, 3), 128.0)
    disp = torch.zeros(h, w)
    disp[100:300, 100:200] = 1.0  # near block
    inv_depth = 1.0 / px.NEAR_FAR + disp * (1 - 1.0 / px.NEAR_FAR)
    scale = 1.0
    sx = 30.0  # focal * baseline in px: near moves 30 px, far moves 30 / NEAR_FAR px
    monkeypatch.setattr(px, "OUT_W", w)
    monkeypatch.setattr(px, "OUT_H", h)
    _, hole, _ = px.splat_frame(rgb, inv_depth, disp, 0, 0, w / 2, h / 2, 0.0, sx, 0.0, 0.0, scale)
    _, still_hole, _ = px.splat_frame(rgb, inv_depth, disp, 0, 0, w / 2, h / 2, 0.0, 0.0, 0.0, 0.0, scale)
    assert not still_hole.any()
    gap = sx * (1 - 1.0 / px.NEAR_FAR)  # 25 px opens behind the block's trailing (right) edge
    # The frame's right edge also opens a strip as wide as the far wall's shift; count only the block's gap.
    block_gap = hole[100:300, 150:260].sum() / 200
    assert block_gap == pytest.approx(gap, abs=1.5)


def test_folder_without_start_photos_fails_loudly(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"")
    (tmp_path / "a.json").write_text(json.dumps({"start": "a.jpg"}))
    with pytest.raises(SystemExit, match="b.mp4"):
        cc.jobs_from_dir(tmp_path)
