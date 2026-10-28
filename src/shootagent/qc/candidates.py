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

from ..imaging.diffmap import FIRE_MIN_PX_FRAC, DiffRegion
from .pipeline import RawAnalysis

HIGH_CONFIDENCE = 0.6
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
            "Re-merge the aligned brackets.",
            FULL, conf, {"naive_gain": round(ra.naive_gain, 3), "bracket_shift_px": shifts,
                         "ghost_score": round(ra.fusion.ghost_score, 4)},
            "ghosting", "regroup_brackets"))
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


def _iou(a: list[float], b: list[float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def build_candidates(ra: RawAnalysis, max_regions: int = 8) -> list[Candidate]:
    out = _grouping_candidates(ra)
    multi_view = len(ra.grouping.groups) > 1 and ra.finished_match.overlay
    # An exterior shot is mostly "window" by the brightness test, which would discount everything.
    exterior = ra.window_mask is not None and float(ra.window_mask.mean()) > 0.35
    if ra.diff is not None and not multi_view:
        regs = []
        for r in ra.diff.regions:
            c = _region_candidate(r, ra, exterior)
            if c is not None and c.confidence >= 0.12:
                regs.append(c)
        regs.sort(key=lambda c: -c.confidence)
        kept: list[Candidate] = []
        for c in regs:  # one candidate per place: a replaced object is both "missing" and "added"
            dup = next((k for k in kept if _iou(k.region, c.region) > 0.4), None)
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
