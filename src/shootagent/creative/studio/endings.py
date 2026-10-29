"""How a reel ends (EndingStyle). Every ending but `loop` plays over the last shot, which keeps moving while it dims
and softens (`grade`) so the card reads; `loop` cuts back to the opening shot instead (see plan.py and camera.py).

    end_card      price, address, the home's facts and a call-to-action pill (reel/render.py's card, sharper)
    question      a big question card that asks for comments
    price_reveal  Reel Studio's price tag lands last (a card job; see cards.py)
    punchline     the line as a bold_center statement
    loop          the line pops over the opening shot and clears before the loop point
"""
from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ...store import Listing
from .fonts import cap_height, clean, font
from .kinetic import Canvas, bold_center, fit_lines, legibility, line_width, pop_words
from .layers import (Element, Overlay, Shadow, State, advance, ease_in_out, ease_out, fade_rise, pop, rounded_rect,
                     text_image, to_sprite)
from .look import INK, WHITE, Look

DIM = {"end_card": 0.52, "question": 0.42, "price_reveal": 0.32, "punchline": 0.30, "loop": 0.0}
BLUR = {"end_card": 1.0, "question": 1.0, "price_reveal": 0.6, "punchline": 0.0, "loop": 0.0}


def grade(style: str, t0: float) -> Callable[[np.ndarray, float], np.ndarray] | None:
    """Frame -> frame from reel time t0 on: darken and soften the shot under the ending's card."""
    dim, blur = DIM.get(style, 0.0), BLUR.get(style, 0.0)
    if dim <= 0 and blur <= 0:
        return None

    def f(img: np.ndarray, t: float) -> np.ndarray:
        p = ease_in_out((t - t0) / 0.7)
        if p <= 0:
            return img
        if blur > 0:
            h, w = img.shape[:2]
            small = cv2.resize(img, (max(1, w // 8), max(1, h // 8)), interpolation=cv2.INTER_AREA)
            soft = cv2.resize(cv2.GaussianBlur(small, (0, 0), 2.0), (w, h), interpolation=cv2.INTER_LINEAR)
            img = cv2.addWeighted(img, 1 - blur * p * 0.85, soft, blur * p * 0.85, 0)
        return cv2.convertScaleAbs(img, alpha=1 - dim * p)
    return f


def facts(listing: Listing) -> str:
    bits = []
    if listing.beds:
        bits.append(f"{listing.beds:g} BED" + ("S" if listing.beds != 1 else ""))
    if listing.baths:
        bits.append(f"{listing.baths:g} BATH" + ("S" if listing.baths != 1 else ""))
    if listing.sqft:
        bits.append(f"{listing.sqft:,} SQ FT")
    return "  ·  ".join(bits)


def end_card(text: str, listing: Listing, t0: float, t1: float, cv: Canvas, look: Look) -> Overlay:
    s = cv.k
    rows: list[tuple[object, float, Callable]] = []  # (sprite, height, anim)
    if listing.price:
        if look.elegant:
            f = font("fraunces", 132 * s, 520)
        else:
            f = font("montserrat", 120 * s, 800)
        img, (ox, oy) = text_image(clean(listing.price), f, WHITE)
        ch = cap_height(f)
        rows.append((to_sprite(img, ox + f.getlength(clean(listing.price)) / 2, oy - ch / 2, legibility(f.size, s)),
                     ch, fade_rise(0.15, None, rise=18 * s, dur=0.55, scale0=0.96)))
    if listing.address:
        f = font("inter", 38 * s, 500)
        a = clean(listing.address)
        img, (ox, oy) = text_image(a, f, (246, 244, 240))
        rows.append((to_sprite(img, ox + f.getlength(a) / 2, oy - cap_height(f) / 2, legibility(f.size * 1.6, s)),
                     cap_height(f), fade_rise(0.3, None, rise=12 * s)))
    line = to_sprite(Image.new("RGBA", (int(120 * s), max(2, int(3 * s))), look.accent + (255,)),
                     60 * s, 1.5 * s)
    rows.append((line, 3 * s, _grow_center(0.4)))
    fx = facts(listing)
    if fx:
        f = font("inter", 33 * s, 650)
        tr = 5 * s
        img, (ox, oy) = text_image(fx, f, WHITE, tracking=tr)
        rows.append((to_sprite(img, ox + advance(fx, f, tr) / 2, oy - cap_height(f) / 2, legibility(f.size * 1.8, s)),
                     cap_height(f), fade_rise(0.48, None, rise=12 * s)))
    cta = clean(text).upper() or "SCHEDULE A TOUR"
    f = font("inter", 36 * s, 720)
    tr = 3.5 * s
    tw = advance(cta, f, tr)
    while tw > cv.max_w * 0.9 and f.size > 16:
        f = font("inter", f.size * 0.92, 720)
        tw = advance(cta, f, tr)
    ph, pw = 108 * s, tw + 116 * s
    pill = rounded_rect(pw, ph, ph / 2, WHITE)
    timg, (ox, oy) = text_image(cta, f, INK, tracking=tr)
    pill.alpha_composite(timg, (int((pw - tw) / 2 - ox), int(ph / 2 + cap_height(f) / 2 - oy)))
    rows.append((to_sprite(pill, pw / 2, ph / 2, (Shadow(blur=18 * s, opacity=0.35, dy=8 * s),)), ph,
                 pop(0.72, None, dur=0.26, start=0.6, overshoot=1.8)))
    gaps = [44 * s, 40 * s, 40 * s, 64 * s, 0]
    total = sum(h for _, h, _ in rows) + sum(gaps[: len(rows) - 1])
    y = cv.H * 0.45 - total / 2
    ov = Overlay(t0, t1, name="end_card")
    for i, (sp, h, anim) in enumerate(rows):
        ov.elements.append(Element(sp, cv.cx, y + h / 2, anim))
        y += h + gaps[min(i, len(gaps) - 1)]
    return ov


def _grow_center(t_in: float):
    def f(t: float):
        if t < t_in:
            return None
        return State(sx=max(0.02, ease_out((t - t_in) / 0.45)), alpha=min(1.0, (t - t_in) / 0.1))
    return f


def question(text: str, t0: float, t1: float, cv: Canvas, look: Look) -> Overlay:
    """A white question card, tilted a little, with a speech-bubble prompt to answer in the comments."""
    s = cv.k
    words = clean(text).split() or ["What", "would", "you", "change?"]
    if not words[-1].endswith("?"):
        words[-1] += "?"
    w_card = cv.W * 0.80
    pad = 60 * s
    f, lines, widths, space = fit_lines(words, "montserrat", 800, 78 * s, w_card - 2 * pad, 4)
    ch, lh = cap_height(f), f.size * 1.14
    qf = font("montserrat", 150 * s, 900)
    q_img, (qox, qoy) = text_image("?", qf, look.card_accent_rgb if look.card_theme == "light" else look.accent)
    q_h = cap_height(qf)
    hint = "COMMENT BELOW"
    hf = font("inter", 28 * s, 700)
    hw = advance(hint, hf, 4 * s)
    text_h = ch + lh * (len(lines) - 1)
    card_h = pad + q_h + 34 * s + text_h + 52 * s + cap_height(hf) + pad
    card = rounded_rect(w_card, card_h, 44 * s, (255, 255, 255))
    y = pad
    card.alpha_composite(q_img, (int(w_card / 2 - qox - qf.getlength("?") / 2), int(y + q_h - qoy)))
    y += q_h + 34 * s
    d = ImageDraw.Draw(card)
    for li, line in enumerate(lines):
        row = " ".join(words[i] for i in line)
        d.text(((w_card - line_width(line, widths, space)) / 2, y + ch + li * lh), row, font=f, fill=INK, anchor="ls")
    y += text_h + 52 * s
    icon = 34 * s
    x0 = (w_card - (icon + 16 * s + hw)) / 2
    _bubble(card, x0, y + cap_height(hf) / 2 - icon / 2, icon, (120, 113, 104))
    himg, (hox, hoy) = text_image(hint, hf, (120, 113, 104), tracking=4 * s)
    card.alpha_composite(himg, (int(x0 + icon + 16 * s - hox), int(y + cap_height(hf) - hoy)))
    sp = to_sprite(card, w_card / 2, card_h / 2, (Shadow(blur=34 * s, opacity=0.4, dy=16 * s),))
    ov = Overlay(t0, t1, name="question")
    ov.elements.append(Element(sp, cv.cx, cv.H * 0.45, pop(0.12, None, dur=0.3, start=0.55, overshoot=1.9,
                                                           rot0=-9.0, rot1=-2.0)))
    return ov


def _bubble(img: Image.Image, x: float, y: float, size: float, color) -> None:
    """A small speech bubble icon (drawn 4x and reduced for clean edges)."""
    k = 4
    big = Image.new("RGBA", (int(size * k), int(size * k)), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    w = size * k
    d.rounded_rectangle([0, 0, w - 1, w * 0.74], radius=w * 0.26, fill=tuple(color) + (255,))
    d.polygon([(w * 0.22, w * 0.66), (w * 0.22, w * 0.98), (w * 0.52, w * 0.70)], fill=tuple(color) + (255,))
    img.alpha_composite(big.resize((int(size), int(size)), Image.Resampling.LANCZOS), (int(x), int(y)))


def punchline(text: str, t0: float, t1: float, cv: Canvas, look: Look) -> Overlay:
    return bold_center(text, "center", t0 + 0.1, t1 + 1.0, cv, look)


def loop_text(text: str, t0: float, t1: float, cv: Canvas, look: Look, ticks) -> Overlay:
    """The line pops over the opening shot and is gone 0.35 s before the end, so the loop point is clean."""
    end = max(t0 + 0.6, t1 - 0.35)
    return pop_words(text, "center", t0 + 0.08, end, cv, look, ticks)


def price_line(text: str, t0: float, t1: float, cv: Canvas, look: Look) -> Overlay:
    """A line under the price tag when the ending's text is too long for the tag itself."""
    return bold_center(text, "bottom", t0 + 0.7, t1 + 1.0, cv, look)
