"""A reel's look follows its music mood, so type, accents and cards agree with what the viewer hears.

Elegant looks (warm, luxe, cinematic) set statements and the end card in Fraunces; the punchy ones set everything in
heavy Montserrat. The accent colours the key word, the active caption word and the sticker; the card accent and
theme go to Reel Studio's templates.
"""
from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]
INK: RGB = (20, 19, 17)
WHITE: RGB = (255, 255, 255)


@dataclass(frozen=True)
class Look:
    mood: str
    elegant: bool
    accent: RGB  # key word, active caption word, countdown number
    sticker_fill: RGB
    sticker_ink: RGB
    card_theme: str  # Reel Studio theme: light | dark
    card_accent: str  # Reel Studio accentColor

    @property
    def card_accent_rgb(self) -> RGB:
        h = self.card_accent.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


LOOKS = {
    "warm": Look("warm", True, (246, 200, 120), WHITE, INK, "light", "#B4532A"),
    "luxe": Look("luxe", True, (231, 201, 145), (24, 22, 20), (231, 201, 145), "dark", "#D9B26F"),
    "cinematic": Look("cinematic", True, (240, 186, 98), WHITE, INK, "dark", "#E0A84F"),
    "upbeat": Look("upbeat", False, (255, 212, 59), (255, 212, 59), INK, "light", "#2F5BFF"),
    "playful": Look("playful", False, (255, 122, 182), (255, 122, 182), WHITE, "light", "#F2559E"),
    "lofi": Look("lofi", False, (255, 190, 118), (255, 243, 224), INK, "light", "#C9702A"),
    "suspense": Look("suspense", False, (255, 82, 70), (255, 70, 58), WHITE, "dark", "#FF4B3E"),
}


def look_for(mood: str) -> Look:
    return LOOKS.get(mood, LOOKS["upbeat"])
