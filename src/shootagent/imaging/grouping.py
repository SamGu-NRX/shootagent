"""Split a bracket set into camera positions, and tell which position(s) the finished photo shows.

Two brackets are the same view when SIFT on CLAHE-equalised luma finds a homography with many inliers
that barely moves the frame (tripod brackets: corner shift <= 3% of the diagonal in the AutoHDR set;
different rooms or viewpoints: < 45 inliers and corner shift >= 40%). Groups are the connected
components of that "same view" graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import features as F

SAME_VIEW_MIN_INLIERS = 25
SAME_VIEW_MAX_SHIFT = 0.12  # corner displacement / diagonal
WEAK_MIN_INLIERS = 10  # attaches an isolated (e.g. very dark) bracket to its best group
FINISHED_MIN_INLIERS = 30
FINISHED_MAX_SHIFT = 0.25  # finished photos are cropped and lens-corrected, so allow more motion
OVERLAY_MIN_INLIERS = 40
OVERLAY_MIN_RATIO = 0.2  # second-best group's inliers relative to the best group's


@dataclass
class Grouping:
    groups: list[list[int]]
    explanation: str
    pairs: dict[tuple[int, int], F.PairMatch] = field(repr=False, default_factory=dict)
    isolated: list[int] = field(default_factory=list)  # brackets attached by weak evidence or left alone

    def to_dict(self) -> dict:
        n = 1 + max((max(g) for g in self.groups), default=-1)
        inl = [[0] * n for _ in range(n)]
        for (i, j), m in self.pairs.items():
            inl[i][j] = inl[j][i] = max(inl[i][j], m.inliers)
        return {"groups": self.groups, "explanation": self.explanation, "inliers": inl, "isolated": self.isolated}


@dataclass
class FinishedMatch:
    per_group: list[dict]  # [{group, best_bracket, inliers, corner_shift, matches}]
    matched: list[int]  # indices into groups that the finished photo shows
    overlay: bool  # finished photo looks like two or more views blended together
    explanation: str

    def to_dict(self) -> dict:
        return {"per_group": self.per_group, "matched": self.matched, "overlay": self.overlay,
                "explanation": self.explanation}


def _same_view(m: F.PairMatch) -> bool:
    return m.H is not None and m.inliers >= SAME_VIEW_MIN_INLIERS and m.corner_shift <= SAME_VIEW_MAX_SHIFT


def _landscape(f: F.Features) -> bool:
    return f.size_wh[0] >= f.size_wh[1]


def pairwise(feats: list[F.Features]) -> dict[tuple[int, int], F.PairMatch]:
    """Match every pair (i -> j for i < j). Pairs that do not look like the same view are re-matched
    j -> i and the better direction kept when either frame is feature-poor or the pair is borderline:
    ratio-test matching is asymmetric, and a very dark bracket with 100 keypoints often fails one
    direction completely while the other finds 60+ inliers."""
    out = {}
    n = len(feats)
    for i in range(n):
        for j in range(i + 1, n):
            m = F.match(feats[i], feats[j])
            sparse = min(len(feats[i].keypoints), len(feats[j].keypoints)) < 800
            if not _same_view(m) and (sparse or m.inliers >= WEAK_MIN_INLIERS):
                r = F.match(feats[j], feats[i])
                if _same_view(r) or r.inliers > m.inliers:
                    r = F.PairMatch(r.good, r.inliers, r.inlier_ratio,
                                    np.linalg.inv(r.H) if r.H is not None else None,
                                    r.corner_shift, r.reproj_px, r.pts_b, r.pts_a)
                    m = r
            out[(i, j)] = m
            # The reverse direction is the inverse homography; store it for alignment lookups.
            out[(j, i)] = F.PairMatch(m.good, m.inliers, m.inlier_ratio,
                                      np.linalg.inv(m.H) if m.H is not None else None,
                                      m.corner_shift, m.reproj_px, m.pts_b, m.pts_a)
    return out


def _span(idx: list[int]) -> str:
    idx = sorted(idx)
    if len(idx) > 2 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{idx[0]}-{idx[-1]}"
    return ", ".join(map(str, idx))


def group_brackets(feats: list[F.Features], pairs: dict[tuple[int, int], F.PairMatch] | None = None) -> Grouping:
    n = len(feats)
    if n == 0:
        raise ValueError("group_brackets needs at least one bracket")
    pairs = pairs if pairs is not None else pairwise(feats)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j), m in pairs.items():
        # A portrait and a landscape frame are never one bracket set (seen in the AutoHDR set: a landscape
        # group and a portrait group of the same room matched through a patch of shared wall).
        if i < j and _same_view(m) and _landscape(feats[i]) == _landscape(feats[j]):
            parent[find(i)] = find(j)
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    groups = [sorted(g) for g in comps.values()]

    # A lone bracket (often the darkest, with few features) joins the multi-bracket group it matches best
    # if the geometry is near-identity, even with few inliers.
    isolated = []
    big = [g for g in groups if len(g) > 1]
    for g in [g for g in groups if len(g) == 1] if big else []:
        i = g[0]
        best, best_n = None, 0
        for gi, other in enumerate(big):
            for j in other:
                m = pairs[(i, j)]
                if (m.H is not None and m.corner_shift <= SAME_VIEW_MAX_SHIFT and m.inliers > best_n
                        and _landscape(feats[i]) == _landscape(feats[j])):
                    best, best_n = gi, m.inliers
        isolated.append(i)
        if best is not None and best_n >= WEAK_MIN_INLIERS:
            big[best].append(i)
            groups.remove(g)
    groups = sorted((sorted(g) for g in groups), key=lambda g: g[0])

    if len(groups) == 1:
        inl = [m.inliers for (i, j), m in pairs.items() if i < j]
        expl = (f"All {n} brackets show the same camera position (median {int(np.median(inl)) if inl else 0} "
                f"matched features per pair, near-identity geometry).")
    else:
        parts = []
        for g in groups:
            within = [pairs[(i, j)].inliers for i in g for j in g if i < j]
            parts.append(f"brackets {_span(g)}" + (f" (median {int(np.median(within))} matches within)" if within else ""))
        across = [pairs[(i, j)].inliers for a in range(len(groups)) for b in range(a + 1, len(groups))
                  for i in groups[a] for j in groups[b]]
        expl = (f"The {n} brackets are {len(groups)} different camera positions: " + "; ".join(parts)
                + f". Across positions at most {max(across) if across else 0} features match and the geometry is "
                "inconsistent, so they must not be fused together.")
    return Grouping(groups, expl, pairs, isolated)


def match_finished(fin: F.Features, feats: list[F.Features], groups: list[list[int]]) -> FinishedMatch:
    """Which bracket group(s) the finished photo shows. Two groups both matching strongly means the
    finished photo blends two camera positions (a wrong-view fusion)."""
    per = []
    for gi, g in enumerate(groups):
        best = {"group": gi, "best_bracket": None, "inliers": 0, "corner_shift": None, "matches": {}}
        for i in g:
            m = F.match(feats[i], fin)
            ok = m.H is not None and m.corner_shift <= FINISHED_MAX_SHIFT
            best["matches"][i] = m.inliers if ok else 0
            if ok and m.inliers > best["inliers"]:
                best.update(best_bracket=i, inliers=m.inliers, corner_shift=round(m.corner_shift, 4))
        per.append(best)
    ranked = sorted(per, key=lambda p: -p["inliers"])
    matched = [p["group"] for p in ranked if p["inliers"] >= FINISHED_MIN_INLIERS]
    overlay = (len(ranked) > 1 and ranked[1]["inliers"] >= OVERLAY_MIN_INLIERS
               and ranked[1]["inliers"] >= OVERLAY_MIN_RATIO * ranked[0]["inliers"])
    if not matched:
        expl = "The finished photo does not match any bracket group geometrically."
    elif overlay:
        expl = (f"The finished photo matches {len([p for p in ranked if p['inliers'] >= OVERLAY_MIN_INLIERS])} different "
                f"camera positions ({', '.join(str(p['inliers']) for p in ranked[:2])} matched features), so it blends "
                "views that should have been separate photos.")
    else:
        expl = f"The finished photo shows bracket group {matched[0]} ({ranked[0]['inliers']} matched features)."
        if len(groups) > 1:
            expl += f" Other groups match at most {ranked[1]['inliers']} features."
    return FinishedMatch(per, matched, overlay, expl)
