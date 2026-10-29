"""Cross-machine generation: plan on the PC, generate on the Mac, check and render on the PC.

`export_requests` writes a self-contained folder: requests.json (each clip request, its fingerprint, and the
exact grok tool arguments the grok backend would send, with photos as relative paths), the photos as JPEG
q90 under inputs/<pixel hash>.jpg, and run_grok.py, a stdlib-only runner. The Mac runs it and sends the
folder back with clips/<fingerprint>.mp4 and results.jsonl. `import_media` verifies each returned clip and
adds it to the replay index, so a FILM_MEDIA=replay film run on the PC serves it by fingerprint and checks
it like any generated clip.

Each generated shot gets two requests: the first attempt and the fixed stricter second attempt
(media.RETRY_TEXT), since the Mac cannot run the checker between them. The fingerprint names the photo
version on the PC (its pixel hash); grok receives a JPEG q90 of it, which keeps the folder small.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import Settings
from ..store import ShootStore
from .media import ClipRequest, GrokBackend, Photo, clip_request, fingerprint
from .spec import Shot
from .video import duration

RUNNER = Path(__file__).with_name("handoff_runner.py")
README = """# Clips to generate for shoot {shoot}

{n} grok Imagine requests ({n_first} first attempts, the rest second attempts). Needs Python 3 and a signed-in
`grok` CLI with Grok's privacy mode opted in for video; nothing else.

    python3 run_grok.py --dry-run     # see what will be sent
    python3 run_grok.py --first-only  # first attempts: about {pct_first}% of the weekly allowance
    python3 run_grok.py               # everything still missing: about {pct}% in total

Calls run one at a time, 20 s apart, and each clip took 41-65 s in the lab. A call whose arguments grok
changed is recorded as failed, not kept. The runner stops if the allowance runs out; rerun it later and it
skips clips already made.

Send the folder back (clips/ and results.jsonl matter) and run on the PC:

    shootagent film-import-media <this folder>
"""
IMAGE_KEYS = ("image", "first_frame", "last_frame")


def export_requests(store: ShootStore, shoot_id: str, shots: list[Shot], out: Path, attempts: int = 2) -> list[dict]:
    """Write the handoff folder for every generated shot in `shots`; returns the request entries."""
    shoot = store.load(shoot_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs").mkdir(exist_ok=True)
    photos: dict[str, Photo] = {}

    def photo(view_id: str) -> Photo:
        if view_id not in photos:
            v = shoot.view(view_id)
            p = Photo.from_file(store.blob_path(shoot_id, v.head_version.blob))
            jpg = out / "inputs" / f"{p.sha}.jpg"
            if not jpg.exists():
                Image.fromarray(np.asarray(p.rgb())).save(jpg, "JPEG", quality=90)
            photos[view_id] = p
        return photos[view_id]

    entries = []
    for i, shot in enumerate(shots):
        if shot.motion != "generated":
            continue
        first, last = photo(shot.view), photo(shot.end_view) if shot.end_view else None
        for n in range(1, attempts + 1):
            req = clip_request(shot.camera or "", shot.aspect, first, last, retry=n > 1)
            fp = fingerprint(req)
            rel = {p.sha: Photo(Path(f"inputs/{p.sha}.jpg"), p.sha) for p in (first, last) if p}
            args = GrokBackend.tool_args(req, rel)
            for k in IMAGE_KEYS:
                if k in args:
                    args[k] = Path(args[k]).as_posix()
            entries.append({"id": f"S{i:02d}-A{n}", "shot": i, "attempt": n, "view": shot.view, "end_view": shot.end_view,
                            "label": shot.label, "mode": req.mode, "fingerprint": fp, "request": req.model_dump(),
                            "args": args, "clip": f"clips/{fp}.mp4"})
    (out / "requests.json").write_text(json.dumps({"shoot": shoot_id, "requests": entries}, indent=1, ensure_ascii=False),
                                       encoding="utf-8")
    shutil.copyfile(RUNNER, out / "run_grok.py")
    n_first = sum(e["attempt"] == 1 for e in entries)
    (out / "README.md").write_text(README.format(shoot=shoot_id, n=len(entries), n_first=n_first,
                                                 pct=2 * len(entries), pct_first=2 * n_first), encoding="utf-8")
    (out / "clips").mkdir(exist_ok=True)
    (out / "clips" / ".keep").write_text("", encoding="utf-8")
    return entries


def import_media(settings: Settings, folder: Path) -> dict:
    """Verify and index the clips a handoff folder came back with. Returns {imported, skipped: [(id, why)]}.

    A clip is imported only when its request rebuilds to the fingerprint it is filed under, results.jsonl
    records a verified call for it with these arguments and the clip's SHA-256, and ffprobe can read it."""
    folder = Path(folder)
    reqs = json.loads((folder / "requests.json").read_text(encoding="utf-8"))["requests"]
    results: dict[str, dict] = {}
    res_path = folder / "results.jsonl"
    if res_path.is_file():
        for line in res_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "ok":
                    results[r["fingerprint"]] = r
    replay = settings.replay_dir
    (replay / "clips").mkdir(parents=True, exist_ok=True)
    idx_path = replay / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.is_file() else {"entries": []}
    known = {e["fingerprint"] for e in index["entries"]}
    imported, skipped = [], []
    for e in reqs:
        clip = folder / e["clip"]
        if not clip.is_file():
            skipped.append((e["id"], "no clip returned"))
            continue
        fp = fingerprint(ClipRequest(**e["request"]))
        if fp != e["fingerprint"] or clip.stem != fp:
            skipped.append((e["id"], f"request rebuilds to {fp}, filed as {e['fingerprint']} / {clip.stem}"))
            continue
        r = results.get(fp)
        if r is None:
            skipped.append((e["id"], "results.jsonl has no verified call for it"))
            continue
        sent = {k: (Path(v).name if k in IMAGE_KEYS else v) for k, v in r["sent"].items()}
        want = {k: (Path(v).name if k in IMAGE_KEYS else v) for k, v in e["args"].items()}
        if sent != want:
            skipped.append((e["id"], "the arguments sent differ from the request"))
            continue
        from .handoff_runner import sha256

        if sha256(clip) != r.get("sha256"):
            skipped.append((e["id"], "the clip's SHA-256 differs from the one recorded when it was made"))
            continue
        try:
            secs = duration(clip)
        except RuntimeError as err:
            skipped.append((e["id"], f"unreadable: {err}"))
            continue
        dest = replay / "clips" / f"handoff-{fp}.mp4"
        shutil.copyfile(clip, dest)
        entry = {"fingerprint": fp, "file": f"clips/{dest.name}", "request": e["request"],
                 "source": f"handoff {folder.name} {e['id']} (grok session {r.get('session')}, {r.get('seconds')} s)"}
        index["entries"] = [x for x in index["entries"] if x["fingerprint"] != fp] + [entry]
        imported.append({"id": e["id"], "fingerprint": fp, "seconds": round(secs, 2), "replaced": fp in known})
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(idx_path)
    return {"imported": imported, "skipped": skipped}
