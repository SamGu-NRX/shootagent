"""Kinetic type: the seven TextStyles of the schema as animated overlays.

Placement follows the platform-safe area of a 9:16 reel: nothing in the top 10% (status bar, account name), the
bottom 18% (caption and audio row) or the right 12% (like, comment, share buttons), so blocks centre on the safe
column rather than the frame. The demo photos are bright and beige, so every style carries its own legibility:
a stroke for heavy type, and for all of them a wide, soft dark scrim baked into the sprite's shadow, which reads
as natural shading rather than a box.

Sizes are written for a 1080 px wide frame and scaled by `Canvas.k`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .fonts import cap_height, clean, font
from .layers import (Element, Overlay, Shadow, Sprite, State, advance, ease_out, fade_rise, hold, pop,
                     rounded_rect, slam, solid, text_image, to_sprite)
from .look import INK, WHITE, Look

Ticks = Callable[[float, float, int], list[float]]  # (from, to, n) -> n reel times for words to land on

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "this", "that", "is", "it", "its",
        "you", "your", "my", "me", "we", "our", "be", "are", "was", "has", "have", "just", "so", "but", "not", "no",
        "from", "by", "as", "into", "than", "then", "there", "here", "what", "who", "how", "why", "when", "all", "every",
        "one", "they", "their", "i", "i'm", "it's", "don't", "can't", "will", "would", "could", "should", "very"}


@dataclass(frozen=True)
class Canvas:
    W: int
    H: int
    captions: bool = False  # spoken captions own the lower third, so bottom text moves up

    @property
    def k(self) -> float:
        return self.W / 1080

    @property
    def cx(self) -> float:  # centre of the safe column (the right 12% is the app's buttons)
        return self.W * 0.475

    @property
    def max_w(self) -> float:
        return self.W * 0.80

    @property
    def left(self) -> float:
        return self.W * 0.08

    def block_top(self, pos: str, h: float) -> float:
        """Top y of a block of height h in the band for `pos`, kept inside the safe area."""
        H = self.H
        if pos == "top":
            y = H * 0.135
        elif pos == "center":
            y = H * 0.465 - h / 2
        else:
            y = H * (0.685 if self.captions else 0.785) - h
        return max(H * 0.105, min(y, H * 0.815 - h))


# ---- words -----------------------------------------------------------------------------------------------------

def split_marked(text: str) -> tuple[list[str], int | None]:
    """Words of `text` and the index of a *starred* word, if any (the room may mark its key word)."""
    words, key = [], None
    for i, w in enumerate(clean(text).split()):
        if re.fullmatch(r"\*+[^*]+\*+[^\w]*", w):
            key = i
            w = w.replace("*", "")
        words.append(w.replace("*", ""))
    return [w for w in words if w], key


def key_word(words: list[str], marked: int | None = None) -> int | None:
    """The word to colour: a starred one, else the first number or price, else the longest content word."""
    if marked is not None:
        return marked
    if len(words) < 2:
        return None
    for i, w in enumerate(words):
        if re.search(r"\d", w):
            return i
    best, best_len = None, 3
    for i, w in enumerate(words):
        core = re.sub(r"[^\w']", "", w).lower()
        if core in STOP:
            continue
        if len(core) >= best_len:
            best, best_len = i, len(core)
    return best


def wrap(widths: list[float], space: float, max_w: float) -> list[list[int]]:
    """Balanced wrap: the fewest lines that fit, with the narrowest widest line (CSS text-wrap: balance)."""
    def greedy(limit: float) -> list[list[int]]:
        lines, cur, cur_w = [], [], 0.0
        for i, w in enumerate(widths):
            nxt = w if not cur else cur_w + space + w
            if cur and nxt > limit:
                lines.append(cur)
                cur, cur_w = [i], w
            else:
                cur.append(i)
                cur_w = nxt
        if cur:
            lines.append(cur)
        return lines

    if not widths:
        return []
    n = len(greedy(max_w))
    lo, hi = max(widths), max(max_w, max(widths))
    for _ in range(18):
        mid = (lo + hi) / 2
        if len(greedy(mid)) <= n:
            hi = mid
        else:
            lo = mid
    return greedy(hi)


def fit_lines(words: list[str], family: str, weight: int, size: float, max_w: float, max_lines: int,
              tracking: float = 0.0, min_size: float = 20.0):
    """Shrink until the words wrap into at most `max_lines` lines no wider than `max_w`."""
    while True:
        f = font(family, size, weight)
        widths = [advance(w, f, tracking) for w in words]
        space = f.getlength(" ") + tracking
        lines = wrap(widths, space, max_w)
        if (len(lines) <= max_lines and max(widths, default=0) <= max_w) or size <= min_size:
            return f, lines, widths, space
        size *= 0.92


def line_width(line: list[int], widths: list[float], space: float) -> float:
    return sum(widths[i] for i in line) + space * max(0, len(line) - 1)


def legibility(size: float, k: float, strength: float = 1.0) -> tuple[Shadow, ...]:
    """A tight drop shadow plus a wide soft scrim: white type reads on a bright beige room."""
    return (Shadow(blur=size * 0.30, opacity=0.30 * strength, spread=int(size * 0.16)),
            Shadow(blur=max(2.0, size * 0.06), opacity=0.55 * strength, dy=max(1.0, size * 0.04)))


# ---- block images (several lines, per-word colours, one sprite) -------------------------------------------------

def block_sprite(lines: list[list[tuple[str, tuple]]], f, line_h: float, stroke: int = 0, stroke_fill=(0, 0, 0),
                 tracking: float = 0.0, align: str = "center", shadows: tuple[Shadow, ...] = ()) -> Sprite:
    """Lines of (word, fill) drawn into one sprite anchored at the centre of the cap-height block."""
    space = f.getlength(" ") + tracking
    widths = [[advance(w, f, tracking) for w, _ in line] for line in lines]
    lws = [sum(ws) + space * max(0, len(ws) - 1) for ws in widths]
    ch = cap_height(f)
    ascent, descent = f.getmetrics()
    pad = stroke + int(f.size * 0.12) + 4
    width = max(lws, default=1)
    block_h = ch + line_h * (len(lines) - 1)
    img = Image.new("RGBA", (int(width + 2 * pad), int(block_h + descent + (ascent - ch) + 2 * pad)), (0, 0, 0, 0))
    top = pad + (ascent - ch)
    for li, line in enumerate(lines):
        x = pad + (width - lws[li]) / 2 if align == "center" else pad
        y = top + ch + li * line_h
        for (word, fill), w in zip(line, widths[li]):
            word_img, (ox, oy) = text_image(word, f, fill, stroke, stroke_fill, tracking)
            img.alpha_composite(word_img, (int(round(x - ox)), int(round(y - oy))))
            x += w + space
    ax = pad + width / 2 if align == "center" else pad
    return to_sprite(img, ax, top + block_h / 2, shadows)


def tagged_words(words: list[str], key: int | None, fill, accent) -> list[tuple[str, tuple]]:
    return [(w, accent if i == key else fill) for i, w in enumerate(words)]


# ---- styles -----------------------------------------------------------------------------------------------------

def pop_words(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look,
              ticks: Ticks | None = None, upper: bool = True) -> Overlay:
    """Words pop in one by one with a scale overshoot, landing on the music; one key word in the accent."""
    words, marked = split_marked(text)
    if upper:
        words = [w.upper() for w in words]
    if not words:
        return Overlay(t_in, t_in)
    key = key_word(words, marked)
    n = len(words)
    size = (150 if n <= 2 else 122 if n <= 4 else 104 if n <= 6 else 92 if n <= 8 else 84) * cv.k
    f, lines, widths, space = fit_lines(words, "montserrat", 800, size, cv.max_w, 3 if n <= 8 else 4)
    size = f.size
    stroke = max(2, round(size * 0.075))
    shadows = legibility(size, cv.k)
    ch = cap_height(f)
    line_h = size * 1.04
    block_h = ch + line_h * (len(lines) - 1)
    top = cv.block_top(pos, block_h)
    times = _word_times(t_in, t_out, n, ticks)
    ov = Overlay(t_in, t_out, name="pop_words")
    for li, line in enumerate(lines):
        x = cv.cx - line_width(line, widths, space) / 2
        base = top + ch + li * line_h
        for i in line:
            img, (ox, oy) = text_image(words[i], f, look.accent if i == key else WHITE, stroke)
            sp = to_sprite(img, ox + widths[i] / 2, oy - ch / 2, shadows)
            ov.elements.append(Element(sp, x + widths[i] / 2, base - ch / 2,
                                       pop(times[i] - t_in, t_out - t_in, dur=0.2, start=0.4, overshoot=2.3)))
            x += widths[i] + space
    return ov


def _word_times(t_in: float, t_out: float, n: int, ticks: Ticks | None) -> list[float]:
    span = max(0.3, t_out - t_in)
    if ticks:
        times = ticks(t_in, t_in + span * 0.7, n)
        if len(times) == n:
            return times
    step = min(0.2, span * 0.6 / max(1, n))
    return [t_in + i * step for i in range(n)]


def bold_center(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look, scale: float = 1.0) -> Overlay:
    """A big statement that scales down into place. A line break (or ' | ') sets the rest as smaller sublines."""
    parts = statement_parts(text)
    if not parts:
        return Overlay(t_in, t_in)
    head, subs = parts[0], parts[1:]
    words, marked = split_marked(head)
    if look.elegant:
        family, weight, base = "fraunces", 560, 150
    else:
        family, weight, base = "montserrat", 900, 140
        words = [w.upper() for w in words]
    n = len(words)
    size = base * cv.k * scale * (1.0 if n <= 3 else 0.8 if n <= 6 else 0.68)
    f, lines, widths, space = fit_lines(words, family, weight, size, cv.max_w, 4)
    key = None if look.elegant else key_word(words, marked)
    tagged = tagged_words(words, key, WHITE, look.accent)
    sp = block_sprite([[tagged[i] for i in line] for line in lines], f, f.size * (1.08 if look.elegant else 1.0),
                      shadows=legibility(f.size, cv.k, 1.15))
    ch = cap_height(f)
    head_h = ch + f.size * (1.08 if look.elegant else 1.0) * (len(lines) - 1)
    # Sublines: tracked capitals in white; a price (or any figure) on the last line is set larger in the accent.
    sub_sprites, sub_hs = [], []
    for j, sub in enumerate(subs):
        figure = j == len(subs) - 1 and bool(re.search(r"[$\d]", sub)) and len(sub) <= 16
        sf = font("inter", max(30 * cv.k, f.size * (0.42 if figure else 0.32)), 680 if figure else 640)
        color = look.accent if figure else WHITE
        sub_sprites.append(block_sprite([[(sub.upper(), color)]], sf, sf.size, tracking=sf.size * (0.06 if figure else 0.14),
                                        shadows=legibility(sf.size * 2.2, cv.k, 1.6)))
        sub_hs.append(cap_height(sf))
    sub_gap = f.size * 0.40
    sub_h = sum(h + sub_gap for h in sub_hs)
    top = cv.block_top(pos, head_h + sub_h)
    ov = Overlay(t_in, t_out, name="bold_center")
    dur = t_out - t_in
    ov.elements.append(Element(sp, cv.cx, top + head_h / 2, _settle(0.0, dur)))
    y = top + head_h + sub_gap
    for j, (s, h) in enumerate(zip(sub_sprites, sub_hs)):
        ov.elements.append(Element(s, cv.cx, y + h / 2, fade_rise(0.25 + 0.12 * j, dur, rise=10 * cv.k, dur=0.5,
                                                                 fade_out=0.25)))
        y += h + sub_gap
    return ov


KICKERS = {"just listed", "new listing", "open house", "just sold", "for sale", "price drop", "coming soon",
           "now available", "for rent", "under contract"}


def statement_parts(text: str | None) -> list[str]:
    """A statement's headline and sublines. Line breaks, ' | ' and ' · ' separate parts, and a listing kicker
    before a colon ("Just listed: Unit 4B · $489,000") is the headline with the rest under it."""
    parts = [clean(p) for p in re.split(r"\n| \| | · | • ", text or "") if clean(p)]
    if parts and ":" in parts[0]:
        head, _, rest = parts[0].partition(":")
        if head.strip().lower() in KICKERS and clean(rest):
            parts = [head.strip(), clean(rest)] + parts[1:]
    return parts


def _settle(t_in: float, t_out: float) -> Callable[[float], State | None]:
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        p = ease_out((t - t_in) / 0.5)
        out = 1.0 - ease_out((t - (t_out - 0.22)) / 0.22) if t > t_out - 0.22 else 1.0
        return State(scale=1.14 - 0.14 * p, alpha=min(1.0, (t - t_in) / 0.22) * out) if out > 0 else None
    return f


def sticker(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look, tilt: float = -4.0) -> Overlay:
    """A slightly tilted rounded tag ("POV:", "WAIT FOR IT") that slaps on with a spin."""
    words = [w.upper() for w in split_marked(text)[0]]
    if not words:
        return Overlay(t_in, t_in)
    f, lines, widths, space = fit_lines(words, "montserrat", 800, 60 * cv.k, cv.W * 0.60, 3)
    ch = cap_height(f)
    line_h = f.size * 1.12
    padx, pady = f.size * 0.55, f.size * 0.42
    lws = [line_width(line, widths, space) for line in lines]
    tw, th = max(lws) + 2 * padx, ch + line_h * (len(lines) - 1) + 2 * pady
    tag = rounded_rect(tw, th, min(th / 2, f.size * 0.42), look.sticker_fill)
    d = ImageDraw.Draw(tag)
    for li, line in enumerate(lines):
        x = (tw - lws[li]) / 2
        d.text((x, pady + ch + li * line_h), " ".join(words[i] for i in line), font=f, fill=look.sticker_ink,
               anchor="ls")
    sp = to_sprite(tag, tw / 2, th / 2, (Shadow(blur=16 * cv.k, opacity=0.38, dy=8 * cv.k),))
    top = cv.block_top(pos, th + 0.1 * th)
    ov = Overlay(t_in, t_out, name="sticker")
    ov.elements.append(Element(sp, cv.cx, top + th / 2, pop(0.0, t_out - t_in, dur=0.26, start=0.25, overshoot=2.6,
                                                             rot0=tilt - 14, rot1=tilt)))
    return ov


def typewriter(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look) -> Overlay:
    """Characters type into a dark box with an accent cursor that keeps blinking once the line is done."""
    words = split_marked(text)[0]
    if not words:
        return Overlay(t_in, t_in)
    f, lines, widths, space = fit_lines(words, "mono", 700, 64 * cv.k, cv.W * 0.76, 4)
    adv = f.getlength("M")
    rows = [" ".join(words[i] for i in line) for line in lines]
    ch = cap_height(f)
    line_h = f.size * 1.38
    padx, pady = f.size * 0.6, f.size * 0.5
    cols = max(len(r) for r in rows)
    bw, bh = cols * adv + adv * 0.9 + 2 * padx, ch + line_h * (len(rows) - 1) + 2 * pady
    top = cv.block_top(pos, bh)
    x0 = cv.cx - bw / 2
    dur = t_out - t_in
    box = to_sprite(rounded_rect(bw, bh, f.size * 0.32, (14, 14, 16, 214)), bw / 2, bh / 2,
                    (Shadow(blur=18 * cv.k, opacity=0.35, dy=8 * cv.k),))
    ov = Overlay(t_in, t_out, name="typewriter")
    ov.elements.append(Element(box, x0 + bw / 2, top + bh / 2, pop(0.0, dur, dur=0.16, start=0.9, overshoot=1.2)))
    n_chars = sum(len(r) for r in rows)
    cps = min(42.0, max(14.0, n_chars / max(0.45, min(1.8, dur * 0.55))))
    t_type = 0.12
    done = t_type + n_chars / cps
    start_char = 0
    for li, row in enumerate(rows):
        img, (ox, oy) = text_image(row, f, WHITE)
        sp = to_sprite(img, ox, oy)
        base = top + pady + ch + li * line_h
        first, count = start_char, len(row)

        def crop(t: float, first=first, count=count, ox=ox) -> tuple[float, float] | None:
            n = int((t - t_type) * cps) - first
            return None if n <= 0 else (0.0, ox + min(count, n) * adv)

        ov.elements.append(Element(sp, x0 + padx, base, hold(0.0, dur, fade_out=0.12), crop))
        start_char += count
    cursor = to_sprite(solid(adv * 0.58, ch * 1.25, look.accent), 0.0, ch * 1.25)
    starts = [sum(len(r) for r in rows[:i]) for i in range(len(rows))]

    def cursor_anim(t: float) -> State | None:
        if t < t_type * 0.5 or t > dur - 0.12:
            return None
        if t > done and int((t - done) / 0.42) % 2 == 1:
            return None
        n = max(0, min(n_chars, int((t - t_type) * cps)))
        li = max(i for i, s in enumerate(starts) if s <= n) if n < n_chars else len(rows) - 1
        col = n - starts[li] if n < n_chars else len(rows[-1])
        return State(dx=col * adv + adv * 0.12, dy=li * line_h + ch * 0.13)

    ov.elements.append(Element(cursor, x0 + padx, top + pady + ch, cursor_anim))
    return ov


def redacted(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look) -> Overlay:
    """Black bars cover the words, then wipe off line by line to reveal them."""
    words = [w.upper() for w in split_marked(text)[0]]
    if not words:
        return Overlay(t_in, t_in)
    f, lines, widths, space = fit_lines(words, "montserrat", 800, 86 * cv.k, cv.max_w * 0.95, 4)
    ch = cap_height(f)
    line_h = f.size * 1.28
    stroke = max(2, round(f.size * 0.06))
    block_h = ch + line_h * (len(lines) - 1)
    top = cv.block_top(pos, block_h)
    dur = t_out - t_in
    ov = Overlay(t_in, t_out, name="redacted")
    bars = []
    for li, line in enumerate(lines):
        row = " ".join(words[i] for i in line)
        lw = line_width(line, widths, space)
        base = top + ch + li * line_h
        img, (ox, oy) = text_image(row, f, WHITE, stroke)
        sp = to_sprite(img, ox, oy, legibility(f.size, cv.k))
        x = cv.cx - lw / 2
        ov.elements.append(Element(sp, x, base, hold(0.12, dur, fade_out=0.14)))
        pad = f.size * 0.16
        bw, bh = lw + 2 * pad, ch + 2 * pad
        bar = to_sprite(rounded_rect(bw, bh, f.size * 0.08, (12, 12, 12)), 0.0, 0.0,
                        (Shadow(blur=6 * cv.k, opacity=0.35, dy=3 * cv.k),))
        bars.append((bar, x - pad, base - ch - pad, bw))
    grow, wipe = 0.14, 0.26
    for li, (bar, bx, by, bw) in enumerate(bars):
        reveal = min(dur * 0.45, 0.45 + 0.16 * li)
        pad_l = bar.ax  # the shadow margin on the left of the bar
        total_w = bar.w

        def crop(t: float, reveal=reveal, pad_l=pad_l, total_w=total_w, bw=bw) -> tuple[float, float] | None:
            if t < 0:
                return None
            right = pad_l + bw * ease_out(t / grow) + (total_w - pad_l - bw) * (1 if t >= grow else 0)
            left = 0.0 if t < reveal else pad_l + bw * ease_out((t - reveal) / wipe)
            return None if right - left < 1 else (left, right)

        ov.elements.append(Element(bar, bx, by, hold(0.0, dur, fade_out=0.1), crop))
    return ov


def label(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look) -> Overlay:
    """The listing reel's elegant room label: a short rule over tracked capitals, lower left. A long label wraps
    (up to three lines) rather than shrinking into the app's buttons."""
    words = clean(text).upper().split()
    if not words:
        return Overlay(t_in, t_in)
    tracking = 7 * cv.k
    width = cv.W * 0.86 - cv.left
    f, lines, widths, space = fit_lines(words, "inter", 650, 40 * cv.k, width, 3, tracking, min_size=24 * cv.k)
    ch = cap_height(f)
    line_h = ch * 1.9
    shadows = (Shadow(blur=54 * cv.k, opacity=0.46, spread=int(34 * cv.k)),
               Shadow(blur=3 * cv.k, opacity=0.65, dy=2 * cv.k))
    rule = to_sprite(solid(52 * cv.k, max(2, 4 * cv.k), look.accent if look.elegant else WHITE), 0.0, 0.0,
                     (Shadow(blur=3 * cv.k, opacity=0.5, dy=1 * cv.k),))
    gap = 22 * cv.k
    block_h = 3 * cv.k + gap + ch + line_h * (len(lines) - 1)
    top = cv.block_top(pos, block_h) - (0.02 * cv.H if pos == "bottom" else 0.0)
    x = cv.left
    dur = t_out - t_in
    ov = Overlay(t_in, t_out, name="label")
    ov.elements.append(Element(rule, x, top, _grow(0.0, dur)))
    for li, line in enumerate(lines):
        img, (ox, oy) = text_image(" ".join(words[i] for i in line), f, WHITE, tracking=tracking)
        ov.elements.append(Element(to_sprite(img, ox, oy, shadows), x, top + 3 * cv.k + gap + ch + li * line_h,
                                   fade_rise(0.08 + 0.06 * li, dur, rise=12 * cv.k, dur=0.5, fade_out=0.3)))
    return ov


def _grow(t_in: float, t_out: float) -> Callable[[float], State | None]:
    def f(t: float) -> State | None:
        if t < t_in:
            return None
        out = 1.0 - ease_out((t - (t_out - 0.3)) / 0.3) if t > t_out - 0.3 else 1.0
        return State(sx=max(0.02, ease_out((t - t_in) / 0.45)), alpha=out) if out > 0 else None
    return f


def countdown(text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look, number: int | None) -> Overlay:
    """A huge number that slams in, with its line under it (numbered formats: '3 things...')."""
    raw = clean(text)
    m = re.match(r"^\s*(?:no\.?\s*|#)?(\d{1,2})\s*[.):\-–—/·•|]?\s+(.*)$", raw, flags=re.I)
    if m:
        number, raw = int(m.group(1)), m.group(2)
    num = str(number if number is not None else 1)
    nf = font("montserrat", 400 * cv.k, 900)
    n_img, (nox, noy) = text_image(num, nf, look.accent, max(3, round(10 * cv.k)))
    nch = cap_height(nf)
    n_sp = to_sprite(n_img, nox + advance(num, nf) / 2, noy - nch / 2, legibility(nf.size * 0.5, cv.k))
    words = [w.upper() for w in split_marked(raw)[0]]
    t_sp, text_h, gap = None, 0.0, 0.0
    if words:
        tf, lines, widths, space = fit_lines(words, "montserrat", 800, 74 * cv.k, cv.max_w, 3)
        t_sp = block_sprite([[(words[i], WHITE) for i in line] for line in lines], tf, tf.size * 1.05,
                            stroke=max(2, round(tf.size * 0.07)), shadows=legibility(tf.size, cv.k))
        text_h = cap_height(tf) + tf.size * 1.05 * (len(lines) - 1)
        gap = nf.size * 0.10
    top = cv.block_top(pos, nch + gap + text_h)
    dur = t_out - t_in
    ov = Overlay(t_in, t_out, name="countdown")
    ov.elements.append(Element(n_sp, cv.cx, top + nch / 2, slam(0.0, dur, start=2.1, dur=0.17, shake=7 * cv.k)))
    if t_sp is not None:
        ov.elements.append(Element(t_sp, cv.cx, top + nch + gap + text_h / 2,
                                   pop(0.16, dur, dur=0.2, start=0.6, overshoot=1.8)))
    return ov


def build(style: str, text: str, pos: str, t_in: float, t_out: float, cv: Canvas, look: Look,
          ticks: Ticks | None = None, number: int | None = None) -> Overlay:
    if style == "pop_words":
        return pop_words(text, pos, t_in, t_out, cv, look, ticks)
    if style == "typewriter":
        return typewriter(text, pos, t_in, t_out, cv, look)
    if style == "sticker":
        return sticker(text, pos, t_in, t_out, cv, look)
    if style == "redacted":
        return redacted(text, pos, t_in, t_out, cv, look)
    if style == "label":
        return label(text, pos, t_in, t_out, cv, look)
    if style == "countdown":
        return countdown(text, pos, t_in, t_out, cv, look, number)
    return bold_center(text, pos, t_in, t_out, cv, look)


def ink_on(rgb) -> tuple:
    """Near-black or white, whichever reads on `rgb`."""
    r, g, b = rgb[:3]
    return INK if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else WHITE
