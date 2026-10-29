"""A ReelConcept becomes a timed plan: shots with cut points, voice lines placed on them, sound effects, music grid.

Order of decisions, each one constraining the next:
  1. every shot gets at least the time its voice line, its card and (shot 0) the hook need; a shot stretched for a
     voice line or a card is reported as a warning
  2. the drop: the shots before it are scaled (within 15%) so the drop lands on a bar line of a grid that starts at
     0; when that would squeeze a voice line, the grid is shifted to the drop instead
  3. other cuts snap to the nearest beat when within 0.12 s and nothing gets too short
  4. the ending is appended (or, for `loop`, a copy of the opening shot that runs into the first frame)
A transition is centred on its cut point, so shots overlap by half a transition on each side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schema import Beat, CardSpec, ReelConcept, TimelineEntry
from . import transitions
from .music import Grid

SNAP = 0.12
HOOK_MIN = 1.5  # the hook text owns at least this much of the first shot
VOICE_AT = 0.1  # the hook's voice starts here
LINE_LEAD = 0.12  # a beat's line starts this long after its cut
LINE_TAIL = 0.22  # and needs this much room after it ends
ENDING = {"end_card": 3.0, "loop": 1.8, "question": 3.0, "price_reveal": 3.0, "punchline": 2.4}


@dataclass
class Shot:
    key: str
    beat: int  # index into concept.beats; -1 for the loop ending's copy of the opening shot
    view: str
    motion: str
    focus: tuple[float, float] | None
    start: float  # cut point
    end: float
    transition: str = "cut"  # how it enters
    t_in: float = 0.0  # transition length centred on `start`
    t_out: float = 0.0
    drop: bool = False

    @property
    def vis0(self) -> float:
        return self.start - self.t_in / 2

    @property
    def vis1(self) -> float:
        return self.end + self.t_out / 2


@dataclass
class Line:
    key: str  # "hook", "b<k>", "end"
    text: str
    at: float  # reel time the line starts


@dataclass
class Effect:
    t: float  # when it lands
    kind: str
    length: float | None = None  # riser / whoosh length
    gain: float = 1.0


@dataclass
class Plan:
    shots: list[Shot]
    beats: list[Beat]  # clamped copies, views resolved
    hook_end: float
    ending_start: float
    total: float
    grid: Grid
    drop: float | None
    lines: list[Line] = field(default_factory=list)
    effects: list[Effect] = field(default_factory=list)
    stops: list[tuple[float, float]] = field(default_factory=list)  # music cut out (record scratch)
    numbers: dict[int, int] = field(default_factory=dict)  # countdown numbers by beat (-1 = hook)
    warnings: list[str] = field(default_factory=list)

    def beat_shot(self, k: int) -> Shot:
        return next(s for s in self.shots if s.beat == k and s.key.startswith("b"))


def card_need(card: CardSpec | None) -> float:
    """Seconds a card needs on screen to land and be read."""
    if card is None or card.kind == "none":
        return 0.0
    k = card.kind
    if k == "price_tag":
        return 1.6
    if k == "stat_count_up":
        return 2.0
    if k == "list_reveal":
        return 1.4 + 0.45 * max(2, min(5, len(card.items or []) or 3))
    if k == "big_quote":
        return 1.2 + 0.2 * len((card.text or "").split())
    reply = bool((card.items or [None])[0] or card.value)
    return 1.4 + len(card.text or "") / 22 + (1.0 if reply else 0.0)  # typing_chat: typing, then the reply lands


def _resolve_views(beats: list[Beat], views: list[str], warnings: list[str]) -> None:
    for k, b in enumerate(beats):
        if b.view not in views:
            alt = views[k % len(views)]
            warnings.append(f"Beat {k + 1} asked for view {b.view!r}, which this shoot does not have; used {alt}.")
            b.view = alt


def make_plan(concept: ReelConcept, views: list[str], voice: dict[str, float]) -> Plan:
    """`views`: the shoot's usable view ids, hero first. `voice`: seconds per spoken line key."""
    warnings: list[str] = []
    beats = [b.model_copy(deep=True).clamp() for b in concept.beats]
    if not beats:
        beats = [Beat(view=views[0], seconds=3.0, motion="push_in")]
        warnings.append("The concept had no shots; the hero photo carries the hook.")
    _resolve_views(beats, views, warnings)
    n = len(beats)
    d = [b.seconds for b in beats]
    need = [0.6] * n

    hook_voice_end = VOICE_AT + voice["hook"] if "hook" in voice else 0.0
    line_at = [LINE_LEAD] * n
    if hook_voice_end:
        line_at[0] = hook_voice_end + 0.15
    need[0] = max(need[0], 1.2, hook_voice_end + 0.2)
    if beats[0].text:  # the first shot's own text follows the hook and needs time of its own
        need[0] = max(need[0], max(HOOK_MIN, hook_voice_end + 0.15) + 1.0)
    for k, b in enumerate(beats):
        if f"b{k}" in voice:
            v_need = line_at[k] + voice[f"b{k}"] + LINE_TAIL
            if v_need > d[k] + 0.05:  # smaller stretches are invisible and not worth a warning
                warnings.append(f"Beat {k + 1}: its {voice[f'b{k}']:.1f} s voiceover did not fit the {d[k]:.1f} s "
                                f"shot; the shot was extended to {v_need:.1f} s.")
            need[k] = max(need[k], v_need)
        c = card_need(b.card)
        if c > max(d[k], need[k]) + 0.05:
            warnings.append(f"Beat {k + 1}: the {b.card.kind} card needs {c:.1f} s; the shot was extended from "
                            f"{d[k]:.1f} s.")
        need[k] = max(need[k], c)
        if k == 0 and hook_voice_end > d[0]:
            warnings.append(f"The hook's voiceover runs {voice['hook']:.1f} s; the first shot was extended.")
        d[k] = max(d[k], need[k])

    # The drop: land it on a bar line.
    grid = Grid(float(concept.music.bpm))
    drop_k = concept.music.drop_beat
    drop_t = None
    if drop_k is not None and not 0 <= drop_k < n:
        warnings.append(f"music.drop_beat {drop_k} is not a shot index; the reel has no drop.")
        drop_k = None
    if drop_k is not None:
        t_drop = sum(d[:drop_k])
        if drop_k == 0:
            drop_t = 0.0
        else:
            bar = grid.bar
            best = None
            for m in range(1, 64):
                target = m * bar
                f = target / t_drop
                if 0.85 <= f <= 1.25 and all(d[j] * f >= need[j] - 1e-6 for j in range(drop_k)):
                    if best is None or abs(target - t_drop) < abs(best - t_drop):
                        best = target
            if best is not None:
                f = best / t_drop
                for j in range(drop_k):
                    d[j] *= f
                drop_t = best
            else:
                grid = Grid(grid.bpm, t_drop % bar)
                drop_t = t_drop

    # Snap the other cuts to the beat.
    T = [0.0]
    for x in d:
        T.append(T[-1] + x)
    for k in range(1, n + 1):
        if drop_k is not None and k == drop_k:
            continue
        # The nearest beat, or the next one when a voice line or card needs the time anyway.
        nxt = grid.beat(int((T[k] - grid.phase) // grid.spb) + 1)
        for g, tol in ((grid.nearest(T[k]), SNAP), (nxt, 0.3)):
            if abs(g - T[k]) > tol or g <= T[k - 1] or g - T[k - 1] < need[k - 1] - 1e-6:
                continue
            if k < n and T[k + 1] - g < need[k] - 1e-6:
                continue
            T[k] = g
            break
    d = [T[k + 1] - T[k] for k in range(n)]

    style = concept.ending.style
    end_len = ENDING.get(style, 2.6)
    if "end" in voice:
        end_len = max(end_len, 0.25 + voice["end"] + 0.5)
    ending_start = T[n]
    total = ending_start + end_len

    shots: list[Shot] = []
    for k, b in enumerate(beats):
        focus = tuple(b.focus) if b.focus else None
        motion = b.motion
        is_drop = drop_k is not None and k == drop_k
        if is_drop:
            motion = "punch_in"
        shots.append(Shot(f"b{k}", k, b.view, motion, focus, T[k], T[k + 1], b.transition if k else "cut",
                          drop=is_drop))
    if style == "loop":
        first = beats[0]
        shots.append(Shot("loop", -1, first.view, first.motion, tuple(first.focus) if first.focus else None,
                          ending_start, total, first.transition))
    else:
        shots[-1].end = total
    for i in range(1, len(shots)):
        a, b = shots[i - 1], shots[i]
        L = transitions.length(b.transition, a.end - a.start, b.end - b.start)
        b.t_in = a.t_out = L

    hook_end = min(shots[0].end - 0.05, max(HOOK_MIN, hook_voice_end + 0.15))
    b0 = beats[0]
    if not (b0.text or (b0.card and b0.card.kind != "none")):
        hook_end = T[1]  # nothing else on the first shot: the hook stays up for all of it (never into the ending)
    plan = Plan(shots, beats, hook_end, ending_start, total, grid, drop_t, warnings=warnings)

    if concept.hook.voiceover and "hook" in voice:
        plan.lines.append(Line("hook", concept.hook.voiceover, VOICE_AT))
    for k, b in enumerate(beats):
        if b.voiceover and f"b{k}" in voice:
            plan.lines.append(Line(f"b{k}", b.voiceover, T[k] + line_at[k]))
    if concept.ending.voiceover and "end" in voice:
        plan.lines.append(Line("end", concept.ending.voiceover, ending_start + 0.25))

    _effects(plan, concept)
    _numbers(plan, concept)
    return plan


def _effects(plan: Plan, concept: ReelConcept) -> None:
    beats, shots = plan.beats, plan.shots
    starts = [plan.beat_shot(k).start for k in range(len(beats))]
    ends = starts[1:] + [plan.ending_start]

    def add(kind: str, t: float, span: float) -> None:
        if kind == "riser":
            length = min(3.0, max(0.6, span))
            plan.effects.append(Effect(t + length, kind, length))
        elif kind != "none":
            plan.effects.append(Effect(t, kind))
        if kind == "record_scratch":
            resume = plan.grid.nearest(t + 0.75)
            plan.stops.append((t + 0.05, max(resume, t + 0.5)))

    add(concept.hook.sfx, 0.0, ends[0])
    for k, b in enumerate(beats):
        add(b.sfx, starts[k], ends[k] - starts[k])
    if plan.drop is not None:
        plan.effects.append(Effect(plan.drop, "bass_drop"))
    for s in shots[1:]:
        own = s.beat >= 0 and beats[s.beat].sfx == "whoosh"
        if s.transition in ("whip", "zoom_through") and not own:
            plan.effects.append(Effect(s.start, "whoosh", 0.45, 0.6))


def _numbers(plan: Plan, concept: ReelConcept) -> None:
    """Countdown numbers: a number written in the text wins; otherwise countdown shots count down to 1."""
    keyed = [(-1, concept.hook.text, concept.hook.style)] + [(k, b.text, b.text_style) for k, b in enumerate(plan.beats)]
    keyed = [(k, t) for k, t, st in keyed if st == "countdown" and t]
    for i, (k, text) in enumerate(keyed):
        m = re.match(r"^\s*(?:no\.?\s*|#)?(\d{1,2})\b", text, flags=re.I)
        plan.numbers[k] = int(m.group(1)) if m else len(keyed) - i


def timeline(plan: Plan, concept: ReelConcept, spoken: set[str], cards: dict[int, str]) -> list[TimelineEntry]:
    """Hook, beat and ending entries for the Reel (voiceover only where it was actually spoken)."""
    out = [TimelineEntry(t0=0.0, t1=round(plan.hook_end, 3), kind="hook", beat=-1, view=plan.beats[0].view,
                         text=concept.hook.text, voiceover=concept.hook.voiceover if "hook" in spoken else None)]
    for k, b in enumerate(plan.beats):
        s = plan.beat_shot(k)
        end = s.end if (k + 1 < len(plan.beats)) else plan.ending_start
        out.append(TimelineEntry(t0=round(s.start, 3), t1=round(end, 3), kind="beat", beat=k, view=b.view,
                                 text=b.text, voiceover=b.voiceover if f"b{k}" in spoken else None,
                                 card=cards.get(k)))
    view = plan.beats[0].view if concept.ending.style == "loop" else plan.beats[-1].view
    out.append(TimelineEntry(t0=round(plan.ending_start, 3), t1=round(plan.total, 3), kind="ending", beat=-1,
                             view=view, text=concept.ending.text,
                             voiceover=concept.ending.voiceover if "end" in spoken else None, card=cards.get(-1)))
    return out
