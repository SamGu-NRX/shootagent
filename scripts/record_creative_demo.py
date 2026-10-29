"""Record a real creative lab as the dashboard's demo replay (web/public/mock/creative/).

The demo mode replays this recording when no backend is running, so the replay is always a real run: its
concepts, critiques, reactions, ratings and simulations come from the lab's state.json unchanged, apart from
quotes the model escaped twice. Reels are re-encoded at 720x1280 to keep the repository light; `--rerender`
renders the named reels again with the current renderer first (same concepts; use it after a cosmetic
renderer fix), and the lab's own files are never modified.

    PYTHONPATH=src python scripts/record_creative_demo.py 64170ca0a0 20261028-212903-d13318 --rerender B
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from shootagent.config import get_settings
from shootagent.creative.schema import LabState
from shootagent.store import ShootStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "mock" / "creative"


def unescape(value):
    if isinstance(value, str):
        return value.replace('\\"', '"')
    if isinstance(value, list):
        return [unescape(v) for v in value]
    if isinstance(value, dict):
        return {k: unescape(v) for k, v in value.items()}
    return value


def encode(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=720:1280:flags=lanczos",
         "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)],
        check=True,
    )


def cover(src: Path, dst: Path) -> None:
    Image.open(src).convert("RGB").resize((720, 1280), Image.Resampling.LANCZOS).save(dst, "JPEG", quality=85)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("shoot")
    ap.add_argument("lab")
    ap.add_argument("--rerender", default="", help="comma-separated reel ids to render again before export")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    store = ShootStore(get_settings().shoots_dir)
    lab_dir = store.shoot_dir(args.shoot) / "creative" / args.lab
    state = LabState.model_validate(unescape(json.loads((lab_dir / "state.json").read_text(encoding="utf-8"))))
    shoot = store.load(args.shoot)
    rerender = {r.strip() for r in args.rerender.split(",") if r.strip()}

    out = args.out
    shutil.rmtree(out, ignore_errors=True)
    (out / "reels").mkdir(parents=True)
    (out / "views").mkdir()

    with tempfile.TemporaryDirectory() as tmp:
        fresh: dict[str, Path] = {}
        if rerender:
            from shootagent.creative.context import make_context
            from shootagent.creative.render import render_concept

            ctx = make_context(args.shoot, lab_dir=Path(tmp), quiet=True)
            concepts = {c.id: c for c in state.concepts}
            for reel in state.reels:
                if reel.id in rerender:
                    again = render_concept(ctx, concepts[reel.concept], reel.id, reel.label, reel.kind)
                    fresh[reel.id] = Path(tmp)
                    print(f"re-rendered {reel.id} ({again.seconds:.1f} s, {len(again.warnings)} warnings)")
        for reel in state.reels:
            base = fresh.get(reel.id, lab_dir)
            encode(base / reel.video, out / reel.video)
            cover(base / reel.poster, out / reel.poster)
            print(f"{reel.id}: {(out / reel.video).stat().st_size / 1e6:.1f} MB")

    views = []
    for v in shoot.views:
        if v.head:
            img = Image.open(store.blob_path(args.shoot, v.head_version.blob)).convert("RGB")
            img.thumbnail((640, 640), Image.Resampling.LANCZOS)
            img.save(out / "views" / f"{v.id}.jpg", "JPEG", quality=84)
            views.append({"id": v.id, "label": v.label})
    (out / "shoot.json").write_text(json.dumps({"id": shoot.id, "title": shoot.title, "listing": shoot.listing.model_dump(),
                                                "views": views}, indent=1), encoding="utf-8")
    (out / "lab.json").write_text(state.model_dump_json(), encoding="utf-8")
    print(f"recorded {args.lab} ({state.status}, {len(state.feed)} messages, {len(state.reels)} reels) to {out}")


if __name__ == "__main__":
    main()
