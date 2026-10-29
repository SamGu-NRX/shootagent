"""The type the studio sets: Montserrat (heavy creator type, studio/fonts), Inter and Fraunces (the listing reel's
pair, reel/fonts) and JetBrains Mono (the typewriter, reelstudio/fonts). All SIL OFL 1.1.

Text from the writers' room can carry characters none of these fonts draw (emoji above all), which PIL renders
as empty boxes, so every string goes through `clean` first.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

HERE = Path(__file__).parent
FILES = {
    "montserrat": HERE / "fonts" / "Montserrat-VF.ttf",
    "inter": HERE.parents[1] / "reel" / "fonts" / "Inter.ttf",
    "fraunces": HERE.parents[1] / "reel" / "fonts" / "Fraunces.ttf",
    "mono": HERE / "reelstudio" / "fonts" / "JetBrainsMono-Bold.ttf",
}
_KEEP = {"Lu", "Ll", "Lt", "Lm", "Lo", "Nd", "Nl", "No", "Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po", "Sc", "Sm", "Zs"}


@lru_cache(maxsize=512)
def font(family: str, size: float, weight: int = 700) -> ImageFont.FreeTypeFont:
    size = max(4, round(size))
    f = ImageFont.truetype(str(FILES[family]), size)
    if family == "montserrat":
        f.set_variation_by_axes([weight])
    elif family == "inter":
        f.set_variation_by_axes([min(32, max(14, size)), weight])
    elif family == "fraunces":  # optical size, weight, softness, wonky
        f.set_variation_by_axes([min(144, max(9, size)), weight, 40, 0])
    return f


def clean(text: str | None) -> str:
    """Printable Latin text only: emoji, pictographs and control characters are dropped, spaces collapsed."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).replace("’", "'").replace("‘", "'")
    kept = "".join(ch if (unicodedata.category(ch) in _KEEP and ord(ch) < 0x2400) else " " for ch in text)
    return re.sub(r"\s+", " ", kept).strip()


def cap_height(f: ImageFont.FreeTypeFont) -> float:
    box = f.getbbox("H", anchor="ls")
    return float(-box[1])
