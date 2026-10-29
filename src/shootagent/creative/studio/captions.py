"""Spoken captions in Reel Studio's "bold-lower" style: heavy Montserrat capitals, white with a solid black outline,
one to three words at a time on the lower third, the word being said in the look's accent colour.

Cue rules follow Reel Studio's caption doctrine: a cue never spans two lines of the script, breaks early at a
sentence end, never leaves one word dangling when it can split 2 + 2, and holds until the next cue starts so the
rail never blinks inside a sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .fonts import cap_height, clean, font
from .kinetic import Canvas
from .layers import Sprite, clamp01, draw, ease_out_back, text_image, to_sprite
from .look import WHITE, Look

MAX_WORDS = 3
TAIL = 0.35  # a line's last cue stays this long after its last word


@dataclass
class Cue:
    words: list[str]
    starts: list[float]  # reel time each word is said
    t0: float
    t1: float


def cues_for_line(words: list[tuple[str, float, float]], next_line: float | None) -> list[Cue]:
    """words: (text, start, end) in reel time for one spoken line."""
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, (w, _, _) in enumerate(words):
        cur.append(i)
        if len(cur) >= MAX_WORDS or re.search(r"[.!?]$", w):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    if len(groups) >= 2 and len(groups[-1]) == 1 and len(groups[-2]) == 3 and \
            not re.search(r"[.!?]$", words[groups[-2][-1]][0]):
        groups[-1].insert(0, groups[-2].pop())
    cues = []
    for gi, g in enumerate(groups):
        t0 = words[g[0]][1]
        if gi + 1 < len(groups):
            t1 = words[groups[gi + 1][0]][1]
        else:
            t1 = words[g[-1]][2] + TAIL
            if next_line is not None:
                t1 = min(t1, next_line - 0.02)
        cues.append(Cue([words[i][0] for i in g], [words[i][1] for i in g], t0, max(t1, t0 + 0.25)))
    return cues


class Captions:
    """The caption rail: one pre-drawn sprite per (cue, active word), drawn over everything else."""

    def __init__(self, cues: list[Cue], cv: Canvas, look: Look):
        self.cues = cues
        self.cy = cv.H * 0.75
        self.cx = cv.cx
        self.sprites: list[list[Sprite]] = []
        base = 100 * cv.k
        for cue in cues:
            text = [clean(w).upper() for w in cue.words]
            text = [re.sub(r"[.,!?;:]+$", "", w) or w for w in text]
            size = base
            f = font("montserrat", size, 700)
            while f.getlength(" ".join(text)) > cv.W * 0.80 and size > base * 0.62:
                size *= 0.94
                f = font("montserrat", size, 700)
            stroke = max(2, round(8 * cv.k * size / base))
            ch = cap_height(f)
            per_word = [self._line(text, active, f, stroke, ch, look) for active in range(len(text))]
            self.sprites.append(per_word)
        self.starts = np.array([c.t0 for c in cues]) if cues else np.zeros(0)

    @staticmethod
    def _line(words: list[str], active: int, f, stroke: int, ch: float, look: Look) -> Sprite:
        """The cue as one sprite with word `active` in the accent, anchored at its centre."""
        space = f.getlength(" ")
        parts, x = [], 0.0
        for i, w in enumerate(words):
            img, (ox, oy) = text_image(w, f, look.accent if i == active else WHITE, stroke)
            parts.append((img, ox, oy, x))
            x += f.getlength(w) + space
        width = x - space
        pad = stroke + 8
        line = Image.new("RGBA", (int(width + 2 * pad + 4), max(p[0].height for p in parts)), (0, 0, 0, 0))
        for img, ox, _, xx in parts:  # same font and stroke, so every word image shares its baseline row
            line.alpha_composite(img, (int(round(pad + xx - ox)), 0))
        return to_sprite(line, pad + width / 2, parts[0][2] - ch / 2)

    def draw(self, frame: np.ndarray, t: float) -> None:
        if not len(self.cues):
            return
        i = int(np.searchsorted(self.starts, t, side="right")) - 1
        if i < 0:
            return
        cue = self.cues[i]
        if t >= cue.t1:
            return
        active = max(j for j, s in enumerate(cue.starts) if s <= t or j == 0)
        p = (t - cue.t0) / 0.12
        scale = 0.86 + 0.14 * ease_out_back(p, 1.6)
        draw(frame, self.sprites[i][active], self.cx, self.cy, scale, 0.0, min(1.0, clamp01((t - cue.t0) / 0.04)))
