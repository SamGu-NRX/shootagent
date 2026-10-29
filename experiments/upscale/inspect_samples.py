from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(r"C:\Users\sgu07\datasets\autohdr\upscale\images")
OUT = Path(r"C:\Users\sgu07\Programming Projects\shootagent-upscale\experiments\upscale\sample_inspection.png")
ids = sorted({p.name.split("_")[0] for p in ROOT.glob("*_hr.png")})[:6]
thumb_w, thumb_h = 360, 240
sheet = Image.new("RGB", (thumb_w * 3, (thumb_h + 28) * len(ids)), "white")
d = ImageDraw.Draw(sheet)
for row, ident in enumerate(ids):
    for col, role in enumerate(("lr", "src", "hr")):
        path = next(ROOT.glob(f"{ident}_{role}.*"))
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((thumb_w, thumb_h))
            x = col * thumb_w + (thumb_w - im.width) // 2
            y = row * (thumb_h + 28) + 20 + (thumb_h - im.height) // 2
            sheet.paste(im, (x, y))
        d.text((col * thumb_w + 8, row * (thumb_h + 28) + 3), f"{ident}_{role}", fill="black")
sheet.save(OUT)
print(OUT)
for ident in ids:
    print(ident, end=" ")
    for role in ("src", "lr", "hr"):
        path = next(ROOT.glob(f"{ident}_{role}.*"))
        with Image.open(path) as im:
            print(f"{role}={im.size}", end=" ")
    print()
