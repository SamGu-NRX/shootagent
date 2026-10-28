"""Autonomous QC for one view of a shoot: candidate defects from bracket evidence, with artifacts.

`analyze_view` is what the agent's `run_qc_checks` tool calls. It never edits the view's versions; it
stores the bracket reference on the view (`view.reference`, registered into the finished photo's frame),
a mask per candidate (source "diff"), and display artifacts, then returns a `ViewAnalysis` and emits
one `finding` trace event per candidate (with `candidate: true`; the agent confirms or dismisses them
with `set_finding`).

`regroup_view` splits a view whose brackets are several camera positions into one view per position.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from ..imaging import brackets as B
from ..imaging import features as F
from ..imaging.align import register
from ..imaging.color import lab_to_rgb, rgb_to_lab
from ..imaging.diffmap import apply_style, fit_style, heatmap
from ..imaging.io import image_size, load_rgb, resize_to
from ..store import Bracket, ShootStore, Version, View, now_iso
from ..trace import TraceRun
from .candidates import HIGH_CONFIDENCE, Candidate, build_candidates
from .pipeline import WORK_LONG_EDGE, RawAnalysis, analyze_arrays

ANALYSIS_VERSION = 1
REGROUP_LONG_EDGE = 4096  # fusion resolution for split views; Mertens on 5 x 66 MP frames does not fit in RAM


@dataclass
class ViewAnalysis:
    view_id: str
    analyzed_blob: str  # the image version that was analysed (head, else finished)
    candidates: list[dict]  # finding dicts: type, explanation, region, confidence, evidence, kind, suggested_fix
    candidate_masks: list[str | None]  # mask id per candidate (None for whole-frame candidates)
    crops: list[str]  # blob per candidate: finished | reference side by side
    reference: str | None  # blob: bracket fusion registered into the finished frame (analysis resolution)
    heatmap: str | None  # blob: difference heatmap over the finished photo
    groups_sheet: str | None  # blob: thumbnails of each bracket group, when brackets split into groups
    groups: list[list[int]]
    grouping: dict
    finished_match: dict
    registration: dict | None
    evs: list[float]
    timings: dict
    summary: str
    high_confidence: int = 0
    version: int = ANALYSIS_VERSION
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def artifacts(self) -> list[str]:
        return [b for b in [self.reference, self.heatmap, self.groups_sheet, *self.crops] if b]


# ---- helpers -------------------------------------------------------------------------------------------
def _analysed_blob(view: View) -> str:
    if view.head is not None:
        return view.head_version.blob
    if view.finished:
        return view.finished
    raise ValueError(f"view {view.id} has neither a finished photo nor versions; ingest it first")


def _load_brackets(store: ShootStore, shoot_id: str, view: View, long_edge: int) -> list[np.ndarray]:
    if not view.brackets:
        raise ValueError(f"view {view.id} has no brackets; QC compares the finished photo with its brackets")
    return [load_rgb(store.blob_path(shoot_id, b.blob), long_edge) for b in sorted(view.brackets, key=lambda b: b.index)]


def _crop_pair(finished: np.ndarray, reference: np.ndarray | None, region: list[float], label: str,
               out_long: int = 900) -> np.ndarray:
    """Finished | reference crops of `region` (padded by 35%), labelled, at most `out_long` px wide."""
    h, w = finished.shape[:2]
    x0, y0, x1, y1 = region
    px, py = 0.35 * (x1 - x0) + 0.02, 0.35 * (y1 - y0) + 0.02
    X0, Y0 = int(max(0, (x0 - px)) * w), int(max(0, (y0 - py)) * h)
    X1, Y1 = int(min(1, (x1 + px)) * w), int(min(1, (y1 + py)) * h)
    a = finished[Y0:Y1, X0:X1]
    b = reference[Y0:Y1, X0:X1] if reference is not None else np.zeros_like(a)
    gap = max(4, a.shape[1] // 100)
    pair = np.hstack([a, np.full((a.shape[0], gap, 3), 255, np.uint8), b])
    k = min(1.0, out_long / pair.shape[1], out_long * 1.6 / max(1, pair.shape[0]))
    if k < 1:
        pair = cv2.resize(pair, (max(1, round(pair.shape[1] * k)), max(1, round(pair.shape[0] * k))),
                          interpolation=cv2.INTER_AREA)
    else:
        pair = pair.copy()
    # Box the region on both halves.
    for off in (0, a.shape[1] + gap):
        p0 = (int((off + x0 * w - X0) * k), int((y0 * h - Y0) * k))
        p1 = (int((off + x1 * w - X0) * k), int((y1 * h - Y0) * k))
        cv2.rectangle(pair, p0, p1, (255, 60, 60), 2, cv2.LINE_AA)
    bar = np.full((28, pair.shape[1], 3), 24, np.uint8)
    cv2.putText(bar, f"finished | brackets   {label}", (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1,
                cv2.LINE_AA)
    return np.vstack([bar, pair])


def _groups_sheet(brackets: list[np.ndarray], groups: list[list[int]], finished: np.ndarray,
                  matched: list[int]) -> np.ndarray:
    """One row per bracket group (thumbnails labelled with source index) plus the finished photo."""
    th = 150
    rows = []
    for gi, g in enumerate(groups):
        tiles = []
        for i in g:
            im = brackets[i]
            t = cv2.resize(im, (max(1, round(th * im.shape[1] / im.shape[0])), th), interpolation=cv2.INTER_AREA)
            cv2.putText(t, str(i), (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(t, str(i), (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 1, cv2.LINE_AA)
            tiles += [t, np.full((th, 4, 3), 255, np.uint8)]
        row = np.hstack(tiles)
        tag = f"group {gi}" + ("  (in finished photo)" if gi in matched else "")
        bar = np.full((24, row.shape[1], 3), 24, np.uint8)
        cv2.putText(bar, tag, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
        rows.append(np.vstack([bar, row]))
    width = max(r.shape[1] for r in rows)
    fin = cv2.resize(finished, (width, round(width * finished.shape[0] / finished.shape[1])), interpolation=cv2.INTER_AREA)
    fin = fin if fin.shape[0] <= 900 else cv2.resize(finished, (round(900 * finished.shape[1] / finished.shape[0]), 900))
    rows = [np.pad(r, ((0, 4), (0, width - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    fin = np.pad(fin, ((0, 0), (0, width - fin.shape[1]), (0, 0)), constant_values=255)
    return np.vstack(rows + [fin])


def _summary(cands: list[Candidate], ra: RawAnalysis) -> str:
    hi = [c for c in cands if c.confidence >= HIGH_CONFIDENCE]
    if not cands:
        return "No disagreement between the finished photo and its brackets beyond normal editing."
    head = (f"{len(hi)} likely defect(s)" if hi else "No high-confidence defects") + f", {len(cands)} candidate(s) to check"
    top = "; ".join(f"{c.type} ({c.confidence:.2f})" for c in cands[:4])
    return f"{head}: {top}."


# ---- analysis ------------------------------------------------------------------------------------------
def analyze_view(store: ShootStore, shoot_id: str, view_id: str, trace: TraceRun | None = None,
                 force: bool = False) -> ViewAnalysis:
    """Compare a view's current image with its brackets and return candidate defects.

    Results are cached in `view.meta["qc"]` keyed by the analysed blob; `force=True` recomputes."""
    shoot = store.load(shoot_id)
    view = shoot.view(view_id)
    blob = _analysed_blob(view)
    cached = view.meta.get("qc")
    if cached and not force and cached.get("analyzed_blob") == blob and cached.get("version") == ANALYSIS_VERSION:
        va = ViewAnalysis(**{**cached, "cached": True})
        _emit(trace, va)
        return va

    t0 = time.perf_counter()
    brackets = _load_brackets(store, shoot_id, view, WORK_LONG_EDGE)
    finished = load_rgb(store.blob_path(shoot_id, blob), WORK_LONG_EDGE)
    ra = analyze_arrays(brackets, finished)
    cands = build_candidates(ra)

    full_w, full_h = image_size(store.blob_path(shoot_id, blob))  # masks match the analysed image
    masks, crops = [], []
    ref_blob = store.put_image(shoot_id, ra.reference_aligned) if ra.reference_aligned is not None else None
    for c in cands:
        if c.mask is not None:
            big = resize_to(c.mask, (int(full_w), int(full_h)))
            masks.append(store.add_mask(shoot_id, view_id, big, source="diff", query=f"qc:{c.type}:{c.kind}").id)
        else:
            masks.append(None)
        crops.append(store.put_image(shoot_id, _crop_pair(finished, ra.reference_aligned, c.region,
                                                          f"{c.type} {c.confidence:.2f}"), fmt="jpg"))
    hm_blob = store.put_image(shoot_id, heatmap(finished, ra.diff), fmt="jpg") if ra.diff is not None else None
    sheet = None
    if len(ra.grouping.groups) > 1:
        sheet = store.put_image(shoot_id, _groups_sheet(brackets, ra.grouping.groups, finished,
                                                        ra.finished_match.matched), fmt="jpg")
    timings = {**ra.timings, "total_with_io": round(time.perf_counter() - t0, 2)}
    findings = [c.to_finding() for c in cands]
    va = ViewAnalysis(
        view_id=view_id, analyzed_blob=blob, candidates=findings, candidate_masks=masks, crops=crops,
        reference=ref_blob, heatmap=hm_blob, groups_sheet=sheet, groups=ra.grouping.groups,
        grouping={k: v for k, v in ra.grouping.to_dict().items()},
        finished_match=ra.finished_match.to_dict(),
        registration=ra.registration.to_dict() if ra.registration else None,
        evs=ra.evs, timings=timings, summary=_summary(cands, ra),
        high_confidence=sum(c.confidence >= HIGH_CONFIDENCE for c in cands),
    )
    record = va.to_dict()
    record.pop("cached")
    primary = ra.grouping.groups[ra.primary]

    def save(shoot):
        v = shoot.view(view_id)
        if ref_blob and (v.reference is None or not v.meta.get("reference_full")):
            v.reference = ref_blob
        v.meta["qc"] = record
        v.meta["qc_at"] = now_iso()
        v.meta["reference_group"] = primary
        for b in v.brackets:
            if b.ev is None and b.index < len(ra.evs):
                b.ev = ra.evs[b.index]

    store.mutate(shoot_id, save)
    _emit(trace, va)
    return va


def _emit(trace: TraceRun | None, va: ViewAnalysis) -> None:
    if trace is None:
        return
    for f, m, crop in zip(va.candidates, va.candidate_masks, va.crops):
        finding = {"type": f["type"], "explanation": f["explanation"], "region": f["region"],
                   "confidence": f["confidence"], "status": "open", "evidence": f["evidence"]}
        trace.emit("finding", view=va.view_id, finding=finding, candidate=True, mask=m, images=[crop],
                   signal=f["kind"], suggested_fix=f["suggested_fix"])


# ---- full-resolution reference for fixes ---------------------------------------------------------------
def reference_for_fix(store: ShootStore, shoot_id: str, view_id: str, long_edge: int = REGROUP_LONG_EDGE,
                      target_blob: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Bracket fusion registered onto the view's current image at that image's full resolution.

    Fuses the reference group (from the last analysis, else all brackets) at `long_edge`, registers it
    on the target image and warps it up to full size. Returns (reference RGB uint8, valid bool mask).
    Raises ValueError when the brackets cannot be registered to the image."""
    shoot = store.load(shoot_id)
    view = shoot.view(view_id)
    blob = target_blob or _analysed_blob(view)
    target = store.get_image(shoot_id, blob)
    th, tw = target.shape[:2]
    group = view.meta.get("reference_group") or [b.index for b in view.brackets]
    by_index = {b.index: b for b in view.brackets}
    imgs = [load_rgb(store.blob_path(shoot_id, by_index[i].blob), long_edge) for i in group if i in by_index]
    feats = [F.detect(im) for im in imgs]
    evs = B.estimate_ev(imgs)
    r = B.pick_reference(evs)
    aligns = B.align_brackets(imgs, r, feats)
    aligned, _ = B.apply_alignment(imgs, aligns, r)
    fused = B.fuse_mertens([a for a, al in zip(aligned, aligns) if al.ok])
    work = load_rgb(store.blob_path(shoot_id, blob), WORK_LONG_EDGE)
    fused_work = resize_to(fused, (round(fused.shape[1] * WORK_LONG_EDGE / max(fused.shape[:2])),
                                   round(fused.shape[0] * WORK_LONG_EDGE / max(fused.shape[:2])))) \
        if max(fused.shape[:2]) > WORK_LONG_EDGE else fused
    reg = register(fused_work, work)
    return reg.warp(fused, (tw, th), interp=cv2.INTER_CUBIC)


# ---- regrouping ----------------------------------------------------------------------------------------
def _style_transfer_stats(src: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Fallback look transfer without correspondence: match L histogram and a/b mean/std."""
    s, r = rgb_to_lab(src), rgb_to_lab(ref)
    out = s.copy()
    sl, rl = s[..., 0].ravel(), r[..., 0].ravel()
    q = np.linspace(0, 1, 257)
    sq, rq = np.quantile(sl, q), np.quantile(rl, q)
    out[..., 0] = np.interp(s[..., 0], sq, rq)
    for c in (1, 2):
        out[..., c] = (s[..., c] - s[..., c].mean()) / (s[..., c].std() + 1e-3) * r[..., c].std() + r[..., c].mean()
    return lab_to_rgb(out)


def regroup_view(store: ShootStore, shoot_id: str, view_id: str, groups: list[list[int]] | None = None,
                 trace: TraceRun | None = None, long_edge: int = REGROUP_LONG_EDGE) -> list[str]:
    """Split a misgrouped view into one new view per bracket group.

    Each new view's finished photo is that group's aligned Mertens fusion restyled toward the original
    finished photo's look: the colour/tone mapping is fitted where the finished photo overlaps the group
    it matches best and then applied to every group (they were shot together, so one grade fits all).
    `groups` are source bracket indices; default: the groups from the last analysis. New views record
    `derived_from` and `groups`; the original view gets `groups` and `meta["split_into"]`."""
    shoot = store.load(shoot_id)
    view = shoot.view(view_id)
    if groups is None:
        groups = (view.meta.get("qc") or {}).get("groups")
        if not groups:
            raise ValueError(f"view {view_id} has no detected groups; run analyze_view first or pass groups")
    by_index = {b.index: b for b in view.brackets}
    missing = sorted({i for g in groups for i in g} - set(by_index))
    if missing:
        raise ValueError(f"view {view_id} has no brackets with source indices {missing}")
    if len(groups) < 2:
        raise ValueError("regroup needs at least two groups; a single group is the view as it is")

    finished = store.get_image(shoot_id, view.finished) if view.finished else None
    fin_work = load_rgb(store.blob_path(shoot_id, view.finished), WORK_LONG_EDGE) if view.finished else None

    fused_all = []
    for g in groups:
        imgs = [load_rgb(store.blob_path(shoot_id, by_index[i].blob), long_edge) for i in g]
        feats = [F.detect(im) for im in imgs]
        evs = B.estimate_ev(imgs)
        r = B.pick_reference(evs)
        aligns = B.align_brackets(imgs, r, feats)
        aligned, _ = B.apply_alignment(imgs, aligns, r)
        fused_all.append(B.fuse_mertens([a for a, al in zip(aligned, aligns) if al.ok]))

    # Fit the finished photo's grade on the group that overlaps it best.
    fit, best = None, None
    if fin_work is not None:
        scores = []
        fin_f = F.detect(fin_work)
        for k, fz in enumerate(fused_all):
            m = F.match(F.detect(fz), fin_f)
            scores.append(m.inliers if m.H is not None else 0)
        best = int(np.argmax(scores))
        if scores[best] >= 30:
            fz = fused_all[best]
            fz_work = resize_to(fz, (round(fz.shape[1] * WORK_LONG_EDGE / max(fz.shape[:2])),
                                     round(fz.shape[0] * WORK_LONG_EDGE / max(fz.shape[:2]))))
            try:
                reg = register(fz_work, fin_work, use_flow=False)
                ref_al, valid = reg.warp(fz_work, (fin_work.shape[1], fin_work.shape[0]))
                fit = fit_style(rgb_to_lab(ref_al), rgb_to_lab(fin_work), valid)
            except ValueError:
                fit = None

    new_ids = []
    base_n = len(groups)
    created: list[View] = []
    for k, (g, fz) in enumerate(zip(groups, fused_all)):
        if fit is not None:
            styled = lab_to_rgb(apply_style(rgb_to_lab(fz), fit))
            how = f"grade fitted on group {best} vs the finished photo"
        elif finished is not None:
            styled = _style_transfer_stats(fz, fin_work)
            how = "tone/colour statistics of the finished photo"
        else:
            styled, how = fz, "plain fusion (no finished photo to match)"
        nid = f"{view_id}-{chr(97 + k)}"
        blob = store.put_image(shoot_id, styled)
        ref_blob = store.put_image(shoot_id, fz)
        nv = View(
            id=nid,
            label=f"{view.label or view.id} ({k + 1}/{base_n})",
            room=view.room,
            brackets=[Bracket(**by_index[i].model_dump()) for i in g],
            finished=blob,
            reference=ref_blob,
            groups=[list(g)],
            derived_from=view_id,
            versions=[Version(id=f"{nid}.0", parent=None, blob=blob, op="regroup",
                              params={"source_view": view_id, "group": list(g), "groups": groups, "style": how},
                              note=f"Fused brackets {g} on their own and styled with {how}.",
                              run=trace.id if trace is not None else None)],
            head=f"{nid}.0",
            meta={**{k2: v for k2, v in view.meta.items() if k2 not in ("qc", "qc_at", "reference_group")},
                  "width": styled.shape[1], "height": styled.shape[0], "regroup_style": how},
        )
        created.append(nv)
        new_ids.append(nid)

    def save(shoot):
        v = shoot.view(view_id)
        existing = {x.id for x in shoot.views}
        clash = [c.id for c in created if c.id in existing]
        if clash:
            raise ValueError(f"views {clash} already exist; view {view_id} was regrouped before")
        v.groups = [list(g) for g in groups]
        v.meta["split_into"] = new_ids
        pos = shoot.views.index(v) + 1
        shoot.views[pos:pos] = created

    store.mutate(shoot_id, save)
    if trace is not None:
        trace.emit("message", view=view_id, text=f"Split {view_id} into {', '.join(new_ids)} (groups {groups}).")
    return new_ids
