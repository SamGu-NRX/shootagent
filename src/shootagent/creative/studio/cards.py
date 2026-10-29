"""Reel Studio animation cards on a shot: CardSpec -> template params -> a transparent .mov -> frames in the reel.

The templates (vendored in reelstudio/animations, MIT) are HTML pages that `render.mjs` steps frame by frame in a
headless Chromium (Playwright), writing a 1080x1920 qtrle .mov with straight alpha. Renders are cached under the
lab by a hash of their params, so the same card is never rendered twice. Each card is decoded once, premultiplied
by ffmpeg, cropped to its visible box and de-duplicated (`drift` is 0, so a settled card is one frame), which keeps
a three-second card in tens of megabytes. If node, Playwright or Chromium is missing or a render fails, the card is
drawn with PIL instead and the reel gets a warning: a card never fails a render.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..schema import CardSpec
from .fonts import cap_height, clean, font
from .kinetic import Canvas, fit_lines, line_width
from .layers import Element, Overlay, Shadow, State, ease_out, fade_rise, pop, rounded_rect, text_image, to_sprite
from .look import Look

ANIMATIONS = Path(__file__).parent / "reelstudio" / "animations"
TEMPLATES = {"price_tag": "price-tag", "stat_count_up": "stat-count-up", "list_reveal": "list-reveal",
             "big_quote": "big-quote", "typing_chat": "typing-terminal"}
CARD_FPS = 30
RENDER_TIMEOUT = 90


@dataclass
class CardJob:
    kind: str
    params: dict
    t0: float  # reel time the card lands
    t1: float  # reel time it is gone
    spec: CardSpec
    region: dict
    key: str = ""
    path: Path | None = None

    def __post_init__(self):
        blob = json.dumps({"template": TEMPLATES[self.kind], "params": self.params}, sort_keys=True)
        self.key = hashlib.sha1(blob.encode()).hexdigest()[:16]


def _label(text: str | None) -> str | None:
    """Card label typography: list separators become commas (a wrapped line never starts with a dot), and a
    number stays with its unit ("640 sq ft" never splits)."""
    t = clean(text)
    if not t:
        return None
    t = re.sub(r"\s+[\u00b7\u2022|]\s+", ", ", t)
    t = re.sub(r"(\d[\d,.]*)\s+(?=[a-zA-Z])", "\\1\u00a0", t)
    return t.replace("sq ft", "sq\u00a0ft")


def _cut(text: str | None, limit: int) -> str | None:
    """Shorten at a word boundary to fit a template's length limit."""
    t = clean(text)
    if not t:
        return None
    if len(t) <= limit:
        return t
    words, out = t.split(), ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > limit - 1:
            break
        out = f"{out} {w}".strip()
    return (out or t[: limit - 1]).rstrip(",;:") + "…"


def region_for(text_pos: str | None) -> dict:
    """A card goes where the shot's text is not, inside the safe area (template px, 1080x1920)."""
    if text_pos == "top":
        return {"x": 70, "y": 640, "w": 840, "h": 600}
    if text_pos == "center":
        return {"x": 70, "y": 220, "w": 840, "h": 560}
    return {"x": 70, "y": 330, "w": 840, "h": 680}


def params_for(spec: CardSpec, price: str | None, look: Look, seconds: float, region: dict) -> dict | None:
    """Template params for a CardSpec, within each template's meta.json limits, or None if it cannot be shown."""
    common = {"theme": look.card_theme, "accentColor": look.card_accent, "drift": 0, "region": region,
              "duration": round(min(30.0, max(0.5, seconds)), 3)}
    k = spec.kind
    if k == "price_tag":
        value = _cut(spec.value or price, 12)
        if not value or value.endswith("…"):
            return None
        p = {"value": value, "text": _label(_cut(spec.text, 40))}
    elif k == "stat_count_up":
        value = clean(spec.value) or (re.search(r"[$\d][\d,.%$kKmM+]*", clean(spec.text)) or [None])[0]
        if not value or len(value) > 14:
            return None
        p = {"value": value, "text": _label(_cut(spec.text, 60))}
    elif k == "list_reveal":
        raw = [clean(i) for i in (spec.items or []) if clean(i)]
        if len(raw) < 2 and spec.text and "," in spec.text:
            raw = [clean(i) for i in spec.text.split(",") if clean(i)]
        # The template sets 4-5 items in two columns of unwrapped text above the head: fine for "Stone kitchen",
        # unreadable for "Bench and hooks by door" (type shrank to ~30 px in a real lab). Long items keep the
        # single big column by stopping at three, which has room for ~28 characters a line.
        if len(raw) >= 4 and max(len(i) for i in raw) <= 12:
            items = raw[:5]
        else:
            items = [_cut(i, 28) for i in raw[:3]]
        if len(items) < 2:
            return None
        title = _cut(spec.text, 50) if spec.items else None
        step = max(0.25, min(0.6, (seconds - 1.2) / len(items)))
        p = {"items": items, "text": title, "itemStep": round(step, 3)}
    elif k == "big_quote":
        text = _cut(spec.text, 120)
        if not text:
            return None
        hl = [_cut(spec.highlight, 40)] if spec.highlight else None
        p = {"text": text, "highlight": hl}
    elif k == "typing_chat":
        text = _cut(spec.text, 160)
        if not text:
            return None
        reply = (spec.items or [None])[0] or spec.value
        p = {"text": text, "style": "chat", "response": _cut(reply, 160), "theme": "light"}
        common = {**common, "theme": "light"}
    else:
        return None
    return {**common, **{a: b for a, b in p.items() if b is not None}}


def node_ok() -> bool:
    return bool(shutil.which("node")) and (ANIMATIONS / "node_modules" / "playwright").is_dir()


def _render_one(job: CardJob, cache: Path) -> str | None:
    """Render `job` to cache/<key>.mov with Reel Studio's CLI. Returns an error string, or None on success."""
    out = (cache / f"{job.key}.mov").resolve()  # absolute: node runs in the templates folder
    if out.exists() and out.stat().st_size > 0:
        job.path = out
        return None
    if not node_ok():
        return "node or Playwright is not installed for the Reel Studio cards"
    cmd = ["node", "render.mjs", TEMPLATES[job.kind], "--params", json.dumps(job.params), "--out", str(out),
           "--fps", str(CARD_FPS)]
    try:
        r = subprocess.run(cmd, cwd=ANIMATIONS, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return f"card render failed: {e}"
    try:
        result = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = {}
    if r.returncode != 0 or "error" in result or not out.exists():
        err = result.get("error", {}).get("message") if isinstance(result.get("error"), dict) else r.stderr
        return f"card render failed: {(err or 'unknown error').strip()[:200]}"
    job.path = out
    return None


def render_all(jobs: list[CardJob], cache: Path) -> list[str | None]:
    cache.mkdir(parents=True, exist_ok=True)
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(3, len(jobs))) as pool:
        return list(pool.map(lambda j: _render_one(j, cache), jobs))


# ---- playback ---------------------------------------------------------------------------------------------------

@dataclass
class CardClip:
    """Decoded card frames: unique premultiplied crops and, per template frame, which crop to show."""

    t0: float
    t1: float
    frames: list[tuple[int, int, np.ndarray]] = field(default_factory=list)  # (x, y, uint8 premultiplied RGBA)
    index: list[int] = field(default_factory=list)

    def draw(self, frame: np.ndarray, t: float) -> None:
        if not (self.t0 <= t < self.t1) or not self.index:
            return
        k = min(len(self.index) - 1, int((t - self.t0) * CARD_FPS + 1e-6))
        x, y, px = self.frames[self.index[k]]
        fade = min(1.0, (self.t1 - t) / 0.12)
        h, w = px.shape[:2]
        H, W = frame.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x0 >= x1 or y0 >= y1:
            return
        s = px[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32) * (fade / 255.0)
        region = frame[y0:y1, x0:x1].astype(np.float32)
        region *= 1.0 - s[..., 3:4]
        region += s[..., :3] * 255.0
        frame[y0:y1, x0:x1] = np.clip(region + 0.5, 0, 255).astype(np.uint8)


def load(path: Path, W: int, H: int, t0: float, t1: float) -> CardClip:
    """Decode a card .mov (premultiplied and scaled to the reel by ffmpeg), crop and de-duplicate its frames."""
    vf = "premultiply=inplace=1" + ("" if (W, H) == (1080, 1920) else f",scale={W}:{H}:flags=area")
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-pix_fmt", "rgba", "-f", "rawvideo",
                             "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    clip = CardClip(t0, t1)
    size = W * H * 4
    prev = None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            img = np.frombuffer(buf, np.uint8).reshape(H, W, 4)
            if prev is not None and np.array_equal(img, prev):
                clip.index.append(clip.index[-1])
                continue
            prev = img
            ys, xs = np.nonzero(img[..., 3] > 0)
            if len(ys) == 0:
                clip.frames.append((0, 0, np.zeros((1, 1, 4), np.uint8)))
            else:
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                clip.frames.append((int(x0), int(y0), np.ascontiguousarray(img[y0:y1, x0:x1])))
            clip.index.append(len(clip.frames) - 1)
    finally:
        proc.stdout.close()
        proc.wait(timeout=30)
    if not clip.index:
        raise RuntimeError(f"could not decode card {path.name}")
    return clip


# ---- the PIL fallback -------------------------------------------------------------------------------------------

def fallback(job: CardJob, cv: Canvas, look: Look) -> Overlay:
    """A clean card drawn with PIL in the spirit of the template: a tilted panel, big type, a quick entrance, and
    for lists the items building one per step."""
    p, k, s = job.params, job.kind, cv.k
    dark = look.card_theme == "dark" and k != "typing_chat"
    card, ink = ((29, 27, 24), (247, 245, 241)) if dark else ((251, 250, 247), (29, 27, 24))
    accent = look.card_accent_rgb
    if k == "typing_chat":
        card, ink = accent, (255, 255, 255)
    pad_x, pad_y, gap = 56 * s, 46 * s, 20 * s
    max_w = job.region["w"] * s * 0.92 - 2 * pad_x
    rows: list[Image.Image] = []
    if k in ("price_tag", "stat_count_up"):
        f = font("inter", (150 if k == "price_tag" else 170) * s, 760)
        while f.getlength(p["value"]) > max_w and f.size > 40:
            f = font("inter", f.size * 0.92, 760)
        rows.append(_big(p["value"], f, accent, s))
        if p.get("text"):
            rows.append(_wrapped(p["text"], "inter", 620, 56 * s, ink, max_w))
    elif k == "list_reveal":
        if p.get("text"):
            rows.append(_wrapped(p["text"], "fraunces", 700, 66 * s, ink, max_w))
        rows += [_item(i, item, font("inter", 60 * s, 700), ink, accent, s) for i, item in enumerate(p["items"])]
    elif k == "big_quote":
        rows.append(_wrapped("\u201c" + p["text"] + "\u201d", "fraunces", 700, 80 * s, ink, max_w))
    else:
        rows.append(_wrapped(p["text"], "inter", 620, 62 * s, ink, max_w))
    panel_w = min(job.region["w"] * s * 0.95, max(r.width for r in rows) + 2 * pad_x)
    panel_h = sum(r.height for r in rows) + gap * (len(rows) - 1) + 2 * pad_y
    titled = k == "list_reveal" and bool(p.get("text"))
    static = rows[:1] if titled else (rows if k != "list_reveal" else [])
    panel = rounded_rect(panel_w, panel_h, 40 * s, card)
    y, placed = pad_y, []
    for r in rows:
        is_item = k == "list_reveal" and not (titled and r is rows[0])
        x = pad_x if is_item else (panel_w - r.width) / 2
        placed.append((r, x, y))
        if r in static:
            panel.alpha_composite(r, (int(x), int(y)))
        y += r.height + gap
    cx = (job.region["x"] + job.region["w"] / 2) * s
    cy = (job.region["y"] + job.region["h"] / 2) * s
    dur = job.t1 - job.t0
    tilt = 0.0 if k == "list_reveal" else -2.0
    ov = Overlay(job.t0, job.t1, name=f"card:{k}")
    shadow = (Shadow(blur=30 * s, opacity=0.32, dy=14 * s),)
    entrance = _slide(dur, 160 * s) if k == "price_tag" else pop(0.0, dur, dur=0.24, start=0.85, overshoot=1.5)
    ov.elements.append(Element(to_sprite(panel, panel_w / 2, panel_h / 2, shadow), cx, cy, _tilted(entrance, tilt)))
    if k == "list_reveal":
        step = float(p.get("itemStep", 0.45))
        items = placed[1:] if titled else placed
        for j, (r, x, y) in enumerate(items):
            at = (0.25 if titled else 0.05) + j * step
            ov.elements.append(Element(to_sprite(r, 0.0, 0.0), cx - panel_w / 2 + x, cy - panel_h / 2 + y,
                                       fade_rise(at, dur, rise=0.0, dur=0.1, fade_out=0.12)))
    return ov


def _tilted(anim, rot: float):
    def f(t: float):
        st = anim(t)
        return None if st is None else State(st.dx, st.dy, st.scale, st.rot + rot, st.alpha, st.sx)
    return f


def _slide(dur: float, dx: float):
    def f(t: float):
        if t < 0:
            return None
        p = ease_out(t / 0.26)
        out = 1.0 if t < dur - 0.12 else max(0.0, (dur - t) / 0.12)
        return State(dx=dx * (1 - p), scale=0.96 + 0.04 * p, alpha=min(1.0, t / 0.05) * out)
    return f


def _big(text: str, f, color, s: float) -> Image.Image:
    img, (_, oy) = text_image(text, f, color)
    return img.crop((0, int(oy - cap_height(f) - 6 * s), img.width, int(oy + 12 * s)))


def _item(i: int, item: str, f, ink, accent, s: float) -> Image.Image:
    chip = 76 * s
    img_t, (ox, oy) = text_image(item, f, ink)
    w, h = int(chip + 26 * s + f.getlength(item) + 8 * s), int(max(chip, cap_height(f) * 1.6))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    big = Image.new("RGBA", (int(chip * 4), int(chip * 4)), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse([0, 0, chip * 4 - 1, chip * 4 - 1], fill=tuple(accent) + (255,))
    out.alpha_composite(big.resize((int(chip), int(chip)), Image.Resampling.LANCZOS), (0, int((h - chip) / 2)))
    ImageDraw.Draw(out).text((chip / 2, h / 2), str(i + 1), font=font("inter", 42 * s, 700), fill=(255, 255, 255),
                             anchor="mm")
    out.alpha_composite(img_t, (int(chip + 26 * s - ox), int(h / 2 + cap_height(f) / 2 - oy)))
    return out


def _wrapped(text: str, family: str, weight: int, size: float, color, max_w: float) -> Image.Image:
    words = text.split()
    f, lines, widths, space = fit_lines(words, family, weight, size, max_w, 4)
    ch, lh = cap_height(f), f.size * 1.16
    w = int(max(line_width(li, widths, space) for li in lines) + 8)
    h = int(ch + lh * (len(lines) - 1) + f.getmetrics()[1] + 4)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    for n, li in enumerate(lines):
        d.text(((w - line_width(li, widths, space)) / 2, ch + n * lh), " ".join(words[i] for i in li), font=f,
               fill=color, anchor="ls")
    return out
