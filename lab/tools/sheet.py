#!/usr/bin/env python3
"""Contact sheet for one clip: source photo(s) on top, then frames at fixed times, as one JPG under 500 KB.

Frames come from ffmpeg; layout and labels from Pillow (Homebrew's ffmpeg here has no drawtext filter).
Usage: uv run --no-project --with pillow python sheet.py --clip c.mp4 --source a.jpg [--source b.jpg] --out s.jpg
"""
import argparse, io, os, subprocess, tempfile

from PIL import Image, ImageDraw, ImageFont

TIMES = [0, 1.2, 2.4, 3.6, 4.8, 5.9]
WIDTH, GAP, LIMIT = 1500, 6, 500 * 1024
FONT = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)


def labelled(img, text):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = d.textbbox((10, 8), text, font=FONT)
    d.rectangle((x0 - 5, y0 - 4, x1 + 5, y1 + 4), fill=(0, 0, 0))
    d.text((10, 8), text, font=FONT, fill=(255, 255, 255))
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--clip", required=True)
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for t in TIMES:
            f = os.path.join(tmp, f"{t}.png")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", a.clip, "-frames:v", "1", f], check=True)
            frames.append(Image.open(f).convert("RGB"))
    w, h = frames[0].size
    cols = 3 if w >= h else 6  # portrait clips fit six across
    tw = (WIDTH - GAP * (cols - 1)) // cols
    th = round(tw * h / w)
    frames = [labelled(f.resize((tw, th), Image.LANCZOS), f"{t} s") for f, t in zip(frames, TIMES)]
    srcs = [Image.open(s).convert("RGB") for s in a.source]
    sh = th * 2 if cols == 3 else round(th * 0.9)
    srcs = [labelled(s.resize((round(s.width * sh / s.height), sh), Image.LANCZOS), "source " + os.path.basename(p))
            for s, p in zip(srcs, a.source)]
    total = sum(s.width for s in srcs) + GAP * (len(srcs) - 1)
    if total > WIDTH:  # shrink the source row to fit
        k = WIDTH / total
        srcs = [s.resize((round(s.width * k), round(s.height * k)), Image.LANCZOS) for s in srcs]
        sh = srcs[0].height
    rows = len(TIMES) // cols
    sheet = Image.new("RGB", (WIDTH, sh + GAP + rows * (th + GAP)), (32, 32, 32))
    x = (WIDTH - (sum(s.width for s in srcs) + GAP * (len(srcs) - 1))) // 2
    for s in srcs:
        sheet.paste(s, (x, 0)); x += s.width + GAP
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % cols) * (tw + GAP), sh + GAP + (i // cols) * (th + GAP)))
    for q in (88, 80, 72, 64, 56, 48, 40):
        buf = io.BytesIO(); sheet.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() < LIMIT:
            break
    open(a.out, "wb").write(buf.getvalue())
    print(a.out, f"{buf.tell() // 1024} KB, quality {q}, {sheet.size[0]}x{sheet.size[1]}")


if __name__ == "__main__":
    main()
