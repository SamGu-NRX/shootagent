"""Store-free QC analysis on arrays: grouping, reference fusion, registration and difference maps.

`qc.analyze` wraps this with the shoot store, artifacts and findings. Keeping the pixel pipeline
separate lets eval and tests run it on files directly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..imaging import brackets as B
from ..imaging import features as F
from ..imaging import grouping as G
from ..imaging.align import Registration, grad_ncc, register
from ..imaging.color import equalized_gray, srgb_to_linear
from ..imaging.diffmap import DiffResult, diff

WORK_LONG_EDGE = 1600


@dataclass
class GroupFusion:
    indices: list[int]  # bracket indices (input order) in this group
    evs: list[float]
    ref: int  # input index of the anchor bracket
    alignments: list[B.BracketAlignment]
    fused: np.ndarray  # RGB uint8 in the anchor bracket's frame, working resolution
    coverage: np.ndarray
    ghost: np.ndarray
    ghost_score: float


@dataclass
class RawAnalysis:
    size_wh: tuple[int, int]  # finished photo at working resolution
    grouping: G.Grouping
    finished_match: G.FinishedMatch
    primary: int  # index into grouping.groups that the reference is built from
    fusion: GroupFusion | None
    registration: Registration | None
    reference_aligned: np.ndarray | None  # fusion warped into the finished frame
    valid: np.ndarray | None
    window_mask: np.ndarray | None  # clipped-in-mid-bracket areas (windows, lamps) in the finished frame
    diff: DiffResult | None
    evs: list[float]  # per input bracket (within-group estimates)
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ghost_finished: np.ndarray | None = None  # bracket disagreement map warped into the finished frame
    naive_gain: float = 0.0  # how much better the finished photo matches an UNaligned fusion (> 0: AutoHDR did not align)


def fuse_group(imgs: list[np.ndarray], indices: list[int], feats: list[F.Features],
               pairs: dict[tuple[int, int], F.PairMatch]) -> GroupFusion:
    sub = [imgs[i] for i in indices]
    evs = B.estimate_ev(sub)
    r = B.pick_reference(evs)
    sub_pairs = {(a, b): pairs[(indices[a], indices[b])] for a in range(len(indices)) for b in range(len(indices))
                 if a != b and (indices[a], indices[b]) in pairs}
    aligns = B.align_brackets(sub, r, [feats[i] for i in indices], sub_pairs)
    # Brackets whose size differs from the anchor are warped onto it by the homography anyway.
    aligned, cover = B.apply_alignment(sub, aligns, r)
    usable = [a for a, al in zip(aligned, aligns) if al.ok]
    fused = B.fuse_mertens(usable)
    ghost, gscore = B.ghost_map(aligned, r, cover)
    for al in aligns:
        al.index = indices[al.index]
    return GroupFusion(indices, evs, indices[r], aligns, fused, cover, ghost, gscore)


def window_mask(brackets: list[np.ndarray], fusion: GroupFusion) -> np.ndarray:
    """Windows and light sources in the anchor frame: pixels at least ~2.6 stops brighter than the scene
    median, measured in the darkest bracket of the group (normalised to EV 0), or clipped even there.
    Editors legitimately replace skies and pull window detail, so differences here are weaker evidence."""
    k = int(np.argmin(fusion.evs))
    idx = fusion.indices[k]
    al = next(a for a in fusion.alignments if a.index == idx)
    anchor = brackets[fusion.ref]
    ah, aw = anchor.shape[:2]
    dark = brackets[idx] if idx == fusion.ref else B.warp_to(brackets[idx], al.H, (aw, ah))[0]
    y = cv2.cvtColor(dark, cv2.COLOR_RGB2GRAY)
    lin = srgb_to_linear(y) * (2.0 ** -fusion.evs[k])
    med = float(np.median(lin)) + 1e-4
    m = ((lin >= 6 * med) | (y >= 250)).astype(np.uint8)
    r = max(3, round(max(ah, aw) / 150))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return m > 0


def _naive_gain(brackets: list[np.ndarray], fusion: GroupFusion, reg: Registration, finished: np.ndarray) -> float:
    """Does the finished photo look like the brackets fused WITHOUT alignment?

    Only meaningful when the camera moved between brackets. Compares gradient correlation of the
    finished photo with the aligned fusion and with a naive (unaligned) fusion, both in the finished
    frame, over the pixels where the two fusions disagree. Returns ncc(naive) - ncc(aligned)."""
    shifts = [np.hypot(a.H[0, 2], a.H[1, 2]) for a in fusion.alignments if a.ok]
    if max(shifts, default=0) < 2.0:
        return 0.0
    anchor = brackets[fusion.ref]
    ah, aw = anchor.shape[:2]
    raw = [cv2.resize(brackets[i], (aw, ah), interpolation=cv2.INTER_AREA) if brackets[i].shape[:2] != (ah, aw)
           else brackets[i] for i, a in zip(fusion.indices, fusion.alignments) if a.ok]
    naive = B.fuse_mertens(raw)
    h, w = finished.shape[:2]
    al, valid = reg.warp(fusion.fused, (w, h))
    nv, valid2 = reg.warp(naive, (w, h))
    g = lambda x: equalized_gray(x)
    diffmask = cv2.absdiff(cv2.GaussianBlur(g(al), (0, 0), 2), cv2.GaussianBlur(g(nv), (0, 0), 2)) > 20
    m = diffmask & valid & valid2
    if m.mean() < 0.005:
        return 0.0
    m = cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    gf = g(finished)
    return float(grad_ncc(gf, g(nv), m) - grad_ncc(gf, g(al), m))


def analyze_arrays(brackets: list[np.ndarray], finished: np.ndarray) -> RawAnalysis:
    """`brackets` and `finished` at working resolution (long edge ~WORK_LONG_EDGE)."""
    t0 = time.perf_counter()
    tm: dict[str, float] = {}
    notes: list[str] = []
    feats = [F.detect(im) for im in brackets]
    fin_feats = F.detect(finished)
    tm["features"] = time.perf_counter() - t0
    pairs = G.pairwise(feats)
    grouping = G.group_brackets(feats, pairs)
    fmatch = G.match_finished(fin_feats, feats, grouping.groups)
    tm["grouping"] = time.perf_counter() - t0 - sum(tm.values())
    primary = fmatch.matched[0] if fmatch.matched else int(np.argmax([len(g) for g in grouping.groups]))
    evs = [0.0] * len(brackets)
    fusion = fuse_group(brackets, grouping.groups[primary], feats, pairs)
    for i, ev in zip(fusion.indices, fusion.evs):
        evs[i] = ev
    for gi, g in enumerate(grouping.groups):
        if gi != primary:
            for i, ev in zip(g, B.estimate_ev([brackets[i] for i in g])):
                evs[i] = ev
    tm["fusion"] = time.perf_counter() - t0 - sum(tm.values())

    h, w = finished.shape[:2]
    reg = ref_al = valid = window = res = None
    try:
        reg = register(fusion.fused, finished)
    except ValueError as e:
        notes.append(f"registration failed: {e}")
    naive_gain = 0.0
    if reg is not None:
        ref_al, valid = reg.warp(fusion.fused, (w, h))
        window, _ = reg.warp(window_mask(brackets, fusion), (w, h))
        ghost_fin, _ = reg.warp((fusion.ghost * 255).astype(np.uint8), (w, h))
        naive_gain = _naive_gain(brackets, fusion, reg, finished)
        tm["register"] = time.perf_counter() - t0 - sum(tm.values())
        res = diff(finished, ref_al, valid)
        tm["diff"] = time.perf_counter() - t0 - sum(tm.values())
    else:
        ghost_fin = None
    tm["total"] = time.perf_counter() - t0
    return RawAnalysis((w, h), grouping, fmatch, primary, fusion, reg, ref_al, valid, window, res, evs,
                       {k: round(v, 2) for k, v in tm.items()}, notes,
                       ghost_finished=(ghost_fin.astype(np.float32) / 255 if ghost_fin is not None else None),
                       naive_gain=naive_gain)
