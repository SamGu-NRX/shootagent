"""Turn raw bracket evidence into candidate findings in AutoHDR's taxonomy.

Confidence is a ranking score, not a calibrated probability. The rules encode what the evidence can and
cannot show:

* Grouping evidence (bracket sets that are different camera positions, a finished photo matching two of
  them, a finished photo matching an unaligned fusion better than an aligned one) is geometric and
  reliable, so it scores high.
* Pixel differences are weaker: editors legitimately replace skies, pull windows, neutralise colour
  casts and declutter. Differences inside windows/light sources, or where the brackets themselves
  disagree (moving fans, people), are scored down. The agent adjudicates every candidate visually.

HIGH_CONFIDENCE is the threshold above which a candidate is presented as a likely defect; clean photos
should produce none.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from ..imaging.color import lab_to_rgb, rgb_to_hex, rgb_to_lab
from ..imaging.diffmap import FIRE_MIN_PX_FRAC, DiffRegion
from .pipeline import RawAnalysis

HIGH_CONFIDENCE = 0.6
EXTERIOR_WINDOW_FRAC = 0.35  # an exterior shot is mostly "window" by the brightness test
# Finished photo correlates this much better with an unaligned merge than an aligned one. The two
# labelled same-view ghosting cases score 0.08 and 0.13; a false alarm on a paint case scored 0.046.
NAIVE_GAIN_MIN = 0.06


@dataclass
class Candidate:
    type: str  # AutoHDR taxonomy family
    explanation: str
    region: list[float]  # normalised [x0, y0, x1, y1] in the finished photo
    confidence: float
    evidence: dict = field(default_factory=dict)
    kind: str = ""  # grouping | ghosting | missing | added | changed | chroma
    suggested_fix: str = ""  # tool name the agent would most likely use
    mask: np.ndarray | None = field(default=None, repr=False)  # bool at analysis resolution

    def to_finding(self) -> dict:
        d = asdict(self)
        d.pop("mask")
        d["confidence"] = round(float(self.confidence), 3)
        d["region"] = [round(float(v), 4) for v in self.region]
        return d


FULL = [0.0, 0.0, 1.0, 1.0]


def _frac(mask: np.ndarray, other: np.ndarray | None) -> float:
    if other is None or not mask.any():
        return 0.0
    return float(other[mask].mean())


def _is_fire(r: DiffRegion) -> tuple[bool, float]:
    """Flame pixels (bright saturated orange/yellow, much brighter than the reference) make up a visible
    part of the region and of the frame."""
    frac = r.stats.get("fire_frac", 0.0)
    return frac >= 0.01 and r.stats.get("fire_px_frac", 0.0) >= FIRE_MIN_PX_FRAC, frac


def _edges_touched(b: list[float], tol: float = 0.015) -> int:
    return int(b[0] <= tol) + int(b[1] <= tol) + int(b[2] >= 1 - tol) + int(b[3] >= 1 - tol)


def _grouping_candidates(ra: RawAnalysis) -> list[Candidate]:
    g, fm = ra.grouping, ra.finished_match
    out = []
    if len(g.groups) > 1:
        conf = 0.95 if fm.overlay else 0.8
        used = [g.groups[i] for i in fm.matched] if fm.matched else []
        if fm.overlay:
            expl = (f"{g.explanation} {fm.explanation} Split the brackets into one photo per camera position.")
        else:
            expl = (f"{g.explanation} {fm.explanation} The other position(s) were dropped or blended in; "
                    "each camera position should be its own finished photo.")
        out.append(Candidate("ghosting_grouping", expl, FULL, conf,
                             {"groups": g.groups, "finished_matches": [p["inliers"] for p in fm.per_group],
                              "overlay": fm.overlay, "used_groups": used},
                             "grouping", "regroup_brackets"))
    elif ra.naive_gain > NAIVE_GAIN_MIN:
        conf = float(np.clip(0.55 + 2.5 * ra.naive_gain, 0, 0.9))
        shifts = {a.index: round(float(np.hypot(a.H[0, 2], a.H[1, 2])), 1) for a in ra.fusion.alignments}
        out.append(Candidate(
            "ghosting_grouping",
            "The camera moved between brackets (up to "
            f"{max(shifts.values()):.0f} px at analysis size) and the finished photo matches an unaligned merge "
            f"better than an aligned one (edge correlation +{ra.naive_gain:.2f}), so edges are doubled. "
            "The brackets are one camera position, so do not regroup: restore the doubled areas from the aligned "
            "bracket reference with restore_from_brackets.",
            FULL, conf, {"naive_gain": round(ra.naive_gain, 3), "bracket_shift_px": shifts,
                         "ghost_score": round(ra.fusion.ghost_score, 4)},
            "ghosting", "restore_from_brackets", ra.naive_mask))
    return out


def _region_candidate(r: DiffRegion, ra: RawAnalysis, exterior: bool) -> Candidate | None:
    win = 0.0 if exterior else _frac(r.mask, ra.window_mask)
    ghost = _frac(r.mask, ra.ghost_finished)
    ev = {**r.to_dict(), "window_frac": round(win, 2), "ref_disagreement": round(ghost, 2)}
    s, area = r.score, r.area
    # Strength combines mean map value and peak; size matters a little (tiny specks are noise).
    strength = float(np.clip(0.6 * s + 0.4 * r.peak, 0, 1)) * float(np.clip(0.6 + 40 * area, 0.6, 1.0))
    trust = (1 - 0.6 * min(1.0, win / 0.5)) * (1 - 0.7 * min(1.0, ghost / 0.35))
    # Small regions on the frame edge are mostly registration and lens-correction residue (crops, warped
    # borders); in the eval, frame-corner "missing" regions were the commonest false alarm.
    edges = _edges_touched(r.bbox)
    if edges and r.area < 0.01:
        trust *= 0.6 if edges == 1 else 0.4
    ev["edges_touched"] = edges
    c = r.stats
    if r.kind == "chroma":
        cool = 170 <= c["reference_hue"] <= 330
        drop = c["finished_chroma"] <= 0.45 * c["reference_chroma"]
        conf = (0.3 + 0.2 * cool + 0.1 * drop + 0.1 * (area >= 0.02)) * trust * (0.6 + 0.4 * strength)
        floor = c["center"][1] > 0.7 and 20 <= c["reference_hue"] <= 95
        sky = r.bbox[1] < 0.01 and c["reference_lab"][0] > 60 and cool
        if sky:
            expl = (f"The sky area at the top of the frame is {c['reference_hex']} in the brackets but "
                    f"{c['finished_hex']} in the finished photo: the sky treatment changed or left remnants here.")
            return Candidate("window_sky", expl, r.bbox, conf, ev, "chroma", "restore_from_brackets", r.mask)
        typ = "wood_floor_color" if floor else "paint_color"
        expl = (f"The brackets show this surface as {c['reference_hex']} (chroma {c['reference_chroma']:.0f}) after "
                f"matching the photo's overall colour grade, but the finished photo renders it {c['finished_hex']} "
                f"(chroma {c['finished_chroma']:.0f}): the colour was neutralised.")
        return Candidate(typ, expl, r.bbox, conf, ev, "chroma", "recolor_region", r.mask)
    if r.kind in ("added", "fire"):
        fire, frac = _is_fire(r)
        if fire or r.kind == "fire":
            # Lit lampshades are warm, bright and brighter than the fusion too, but they are bright even in
            # the darkest bracket (window/light mask); real added fire is not.
            # Flames sit in a dark firebox: in the eval, real added fire had reference L 19-29 around it,
            # lamp glow false alarms 47-52.
            dark_box = c["reference_lab"][0] <= 40
            conf = ((0.55 + 0.35 * strength) * (1 - 0.7 * min(1.0, ghost / 0.35)) * (1 - 0.9 * min(1.0, win / 0.3))
                    * (0.6 if edges else 1.0) * (1.0 if dark_box else 0.5))
            ev["dark_surround"] = dark_box
            expl = ("Bright warm flame-like pixels in the finished photo where the brackets show no fire "
                    f"(finished {c['finished_hex']}, brackets {c['reference_hex']}). Fire was added; check it was requested "
                    "and that the object is a fireplace.")
            return Candidate("fireplace", expl, r.bbox, conf, {**ev, "fire_frac": round(frac, 3)}, "added",
                             "restore_from_brackets", r.mask)
        if win > 0.5:
            expl = ("Detail in a window or light source that the brackets do not contain: likely a sky replacement or "
                    "window pull. Usually intended; check for a mask edge or remnants.")
            return Candidate("window_sky", expl, r.bbox, 0.35 * strength, ev, "added", "restore_from_brackets", r.mask)
        expl = ("Structure in the finished photo that is not in the brackets (an editing artifact, a pasted patch or "
                f"an added object). Finished {c['finished_hex']} vs brackets {c['reference_hex']}.")
        return Candidate("camera_person_removal", expl, r.bbox, 0.45 * strength * trust, ev, "added",
                         "restore_from_brackets", r.mask)
    if r.kind == "missing":
        bright_top = r.bbox[1] < 0.01 and r.stats["reference_lab"][0] > 65
        if win > 0.5 or bright_top:
            expl = ("Outdoor or window detail present in the brackets is missing from the finished photo (sky painted "
                    "over a non-sky area, or a window blown out).")
            return Candidate("window_sky", expl, r.bbox, 0.45 * strength * (1 - 0.7 * min(1.0, ghost / 0.35)),
                             ev, "missing", "restore_from_brackets", r.mask)
        # A saturated object that lost its colour (a red sign painted over) is strong evidence.
        lost = c["reference_chroma"] >= 25 and c["finished_chroma"] < 0.4 * c["reference_chroma"]
        conf = min(0.95, (0.35 + 0.45 * strength + 0.2 * lost) * trust)
        ev["colour_lost"] = bool(lost)
        expl = ("Something visible in every bracket is missing from the finished photo here (finished "
                f"{c['finished_hex']}, brackets {c['reference_hex']}). If it is a real object (lamp, sign, furniture), "
                "the removal was a mistake; restore it from the brackets.")
        return Candidate("camera_person_removal", expl, r.bbox, conf, ev, "missing", "restore_from_brackets", r.mask)
    if r.kind == "changed":
        expl = ("Texture here differs between the finished photo and the brackets (replaced content, smearing or a "
                "patch).")
        return Candidate("camera_person_removal", expl, r.bbox, 0.35 * strength * trust, ev, "changed",
                         "restore_from_brackets", r.mask)
    return None


# Wall paint check. The diff map's chroma signal is measured after a global colour fit, and when an editor's
# white balance warms the whole room, a pale blue wall loses its colour together with everything else, so
# the fit absorbs it: on AutoHDR paint cases 0029, 0032 and 0033 the chroma map stayed empty over the
# labelled wall. This check instead compares raw bracket colour with the finished colour on segmented
# walls. Thresholds come from the six paint cases and four clean controls of the AutoHDR set, with no
# held-out data, so they are first guesses: labelled blue walls had bracket chroma 3.4-7.4 and lost 4.9-9.7
# of it; on the controls only a window wall (0018, 44% of its mask glass) passed those two tests.
# Warm walls are not checked: neutralising a tungsten-warm wall is ordinary white balance, and on the
# defect set warm walls passed the chroma tests in 7 cases, none of them labelled.
WALL_MIN_CHROMA = 3.0  # Lab chroma of the wall in the brackets
WALL_MIN_LOSS = 3.5  # chroma the finished wall lost along the bracket hue
WALL_MAX_WINDOW_FRAC = 0.15  # walls mostly made of windows take the sky's colour; labelled walls were <= 0.03
WALL_MIN_AREA = 0.005  # fraction of the frame


def hue_name(a: float, b: float) -> str:
    """Rough colour name for a Lab a/b direction, for explanations the agent reads."""
    h = float(np.degrees(np.arctan2(b, a)) % 360)
    for limit, name in ((25, "pink-red"), (70, "orange-beige"), (105, "yellow"), (165, "green"), (215, "teal"),
                        (295, "blue"), (340, "purple"), (360, "pink-red")):
        if h < limit:
            return name
    return "pink-red"


def _median_lab(lab: np.ndarray, m: np.ndarray) -> np.ndarray:
    return np.median(lab[m], axis=0)


def wall_paint_candidates(reference: np.ndarray, finished: np.ndarray, valid: np.ndarray,
                          walls: list[np.ndarray], neutral: np.ndarray | None = None,
                          window: np.ndarray | None = None) -> list[Candidate]:
    """Walls whose paint colour in the brackets was neutralised in the finished photo.

    `reference` is the raw bracket fusion registered into the finished frame (no colour fit), `walls` bool
    masks of wall surfaces in that frame, `neutral` a mask of a surface that should be neutral (the ceiling),
    reported as evidence so the agent can tell paint from a colour cast. Windows and clipped pixels are left
    out of every measurement. Only cool hues (Lab hue 170-330 degrees) are checked."""
    ref_lab, fin_lab = rgb_to_lab(reference), rgb_to_lab(finished)
    usable = valid & (fin_lab[..., 0] > 15) & (fin_lab[..., 0] < 97) & (ref_lab[..., 0] > 5)
    if window is not None:
        usable &= ~window
    h, w = valid.shape
    neutral_ev = None
    if neutral is not None and (neutral & usable).sum() >= WALL_MIN_AREA * h * w:
        nr, nf = _median_lab(ref_lab, neutral & usable), _median_lab(fin_lab, neutral & usable)
        neutral_ev = {"ceiling_ab_brackets": [round(float(v), 1) for v in nr[1:]],
                      "ceiling_ab_finished": [round(float(v), 1) for v in nf[1:]]}
    out = []
    for wall in walls:
        m = wall & usable
        if m.sum() < WALL_MIN_AREA * h * w:
            continue
        if window is not None and float(window[wall].mean()) > WALL_MAX_WINDOW_FRAC:
            continue
        r, f = _median_lab(ref_lab, m), _median_lab(fin_lab, m)
        c_ref = float(np.hypot(r[1], r[2]))
        if c_ref < WALL_MIN_CHROMA or not 170 <= np.degrees(np.arctan2(r[2], r[1])) % 360 <= 330:
            continue
        along = float((f[1] * r[1] + f[2] * r[2]) / c_ref)  # finished colour along the bracket hue
        loss = c_ref - along
        if loss < WALL_MIN_LOSS:
            continue
        name = hue_name(r[1], r[2])
        # The bracket hue at the finished photo's lightness: what to recolour the wall back to. (The brackets
        # colour-matched to the finished photo frame-wide came out almost grey on 0029 and 0033, so that
        # target would leave the wall as delivered.)
        target = rgb_to_hex(lab_to_rgb(np.array([[[f[0], r[1], r[2]]]], np.float32))[0, 0])
        fin_hex = rgb_to_hex(lab_to_rgb(f.reshape(1, 1, 3).astype(np.float32))[0, 0])
        conf = float(np.clip(0.6 + 0.03 * (loss - WALL_MIN_LOSS), 0.6, 0.8))
        ev = {"kind": "wall_paint", "area": round(float(m.mean()), 4), "reference_hex": target, "finished_hex": fin_hex,
              "wall_ab_brackets": [round(float(v), 1) for v in r[1:]],
              "wall_ab_finished": [round(float(v), 1) for v in f[1:]],
              "reference_chroma": round(c_ref, 1), "finished_chroma": round(float(np.hypot(f[1], f[2])), 1),
              "chroma_lost": round(loss, 1), "bracket_hue": name, **(neutral_ev or {})}
        expl = (f"Segmented wall: {name} in the brackets (chroma {c_ref:.1f}; {target} at the finished lightness) "
                f"but {fin_hex} in the finished photo (chroma {ev['finished_chroma']}): it lost {loss:.1f} of its "
                f"colour along its own hue.")
        if neutral_ev:
            nr_ab, nf_ab = neutral_ev["ceiling_ab_brackets"], neutral_ev["ceiling_ab_finished"]
            expl += (f" The ceiling measures a/b {nr_ab} in the brackets and {nf_ab} in the finished photo, against "
                     f"the wall's {ev['wall_ab_brackets']} and {ev['wall_ab_finished']}.")
        out.append(Candidate("paint_color", expl, _mask_bbox(m), conf, ev, "chroma", "recolor_region", m))
    return out


def _mask_bbox(m: np.ndarray) -> list[float]:
    ys, xs = np.nonzero(m)
    h, w = m.shape
    return [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]


def _overlap_smaller(a: list[float], b: list[float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / small if small > 0 else 0.0


def _same_place(a: Candidate, b: Candidate) -> bool:
    """One candidate per place: a replaced object is both "missing" and "added", and a chroma blob on a wall
    duplicates the wall's own paint candidate."""
    if _iou(a.region, b.region) > 0.4:
        return True
    return a.kind == b.kind == "chroma" and _overlap_smaller(a.region, b.region) > 0.7


def _iou(a: list[float], b: list[float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def build_candidates(ra: RawAnalysis, max_regions: int = 8, extra: list[Candidate] = ()) -> list[Candidate]:
    """`extra`: region candidates from checks outside the diff map (wall paint); they are de-duplicated and
    capped together with the diff regions."""
    out = _grouping_candidates(ra)
    multi_view = len(ra.grouping.groups) > 1 and ra.finished_match.overlay
    # An exterior shot is mostly "window" by the brightness test, which would discount everything.
    exterior = ra.window_mask is not None and float(ra.window_mask.mean()) > EXTERIOR_WINDOW_FRAC
    if ra.diff is not None and not multi_view:
        regs = list(extra)
        for r in ra.diff.regions:
            c = _region_candidate(r, ra, exterior)
            if c is not None and c.confidence >= 0.12:
                regs.append(c)
        regs.sort(key=lambda c: -c.confidence)
        kept: list[Candidate] = []
        for c in regs:
            dup = next((k for k in kept if _same_place(k, c)), None)
            if dup is None:
                kept.append(c)
            else:
                dup.evidence.setdefault("also", []).append(c.kind)
        out += kept[:max_regions]
    if ra.registration is None and not out:
        out.append(Candidate("ghosting_grouping", "The finished photo does not match the brackets geometrically; it may "
                             "come from a different capture.", FULL, 0.7,
                             {"notes": ra.notes}, "grouping", "regroup_brackets"))
    out.sort(key=lambda c: -c.confidence)
    return out
