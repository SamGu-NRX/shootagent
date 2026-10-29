"""Burned-in disclosure labels for photos used outside the MLS (social posts, flyers, print).

MLS systems carry the disclosure in the photo's remarks; a social post or a flyer has nowhere else to put it.
The label is a strip along the bottom edge: "Digitally altered" (or "Virtually staged"), what changed, and a QR
code to the unaltered original. Fonts are the reel's bundled Inter.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

from ..reel.render import font


def qr_image(url: str, size: int) -> Image.Image:
    import segno

    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=10, border=2, dark="#111111", light="#ffffff")
    buf.seek(0)
    img = Image.open(buf).convert("RGB")
    return img.resize((size, size), Image.Resampling.NEAREST)


def _fit(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> str:
    if draw.textlength(text, font=f) <= width:
        return text
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip(" ,;:") + "…"


def burn_label(img: np.ndarray | Image.Image, headline: str, detail: str, url: str | None) -> Image.Image:
    """The photo with a disclosure strip along its bottom edge. The strip covers the bottom ~9% of the frame on a
    translucent dark band; the photo above it is untouched."""
    pil = (Image.fromarray(img) if isinstance(img, np.ndarray) else img).convert("RGB")
    w, h = pil.size
    short = min(w, h)
    band = max(64, round(short * 0.11))
    pad = round(band * 0.14)
    out = pil.copy()
    overlay = Image.new("RGBA", (w, band), (12, 12, 12, 190))
    out.paste(Image.new("RGB", (w, band), (12, 12, 12)), (0, h - band), Image.new("L", (w, band), 190))
    del overlay
    draw = ImageDraw.Draw(out)
    qr_size = band - 2 * pad if url else 0
    text_right = w - pad * 2 - (qr_size + pad if url else 0)
    title_f = font("Inter", max(14, round(band * 0.30)), 650)
    body_f = font("Inter", max(11, round(band * 0.19)), 450)
    x = pad * 2
    y1 = h - band + pad
    draw.text((x, y1), _fit(draw, headline, title_f, text_right - x), font=title_f, fill=(255, 255, 255))
    y2 = y1 + round(band * 0.36)
    draw.text((x, y2), _fit(draw, detail, body_f, text_right - x), font=body_f, fill=(225, 225, 225))
    if url:
        y3 = y2 + round(band * 0.24)
        short_url = url.split("://", 1)[-1]
        draw.text((x, y3), _fit(draw, "Original photo: " + short_url, body_f, text_right - x), font=body_f,
                  fill=(190, 190, 190))
        out.paste(qr_image(url, qr_size), (w - pad - qr_size, h - band + pad))
    return out
