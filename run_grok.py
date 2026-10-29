#!/usr/bin/env python3
"""Make the clips in requests.json with the grok CLI, one at a time, and verify every call.

Copied into each generation handoff folder by `shootagent film-export-requests`. It needs Python 3.9+
and a signed-in `grok` on PATH (or GROK_BIN), nothing else. Port of lab/tools/film.py (explore/media-tests)
and shootagent.film.media.GrokBackend: grok's headless mode puts a model between us and the Imagine tool,
so after each call the session's own event log must show exactly one call with exactly our arguments; a
mismatch is a failure, never a clip.

    python3 run_grok.py              # every request without a clip yet: all first attempts, then second ones
    python3 run_grok.py --first-only # first attempts only (half the allowance)
    python3 run_grok.py --only S01-A1 S03-A1
    python3 run_grok.py --dry-run    # print what would be sent

Writes clips/<fingerprint>.mp4 and appends one line per call to results.jsonl. Rerunning skips clips
already on disk. Each 6 s clip took 41-65 s and about 2% of the SuperGrok weekly allowance in the lab.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"video": "image_to_video", "ref": "reference_to_video"}
IMAGE_KEYS = ("image", "first_frame", "last_frame")
MIN_GAP_S = 20  # lab/tools/film.py: no evidence for how fast the allowance tolerates calls; bursts buy nothing
TIMEOUT_S = 900
SESSIONS = Path.home() / ".grok" / "sessions"


def classify(text: str) -> str:
    t = text.lower()
    if "zero data retention" in t or "zdr" in t:
        return "zdr"  # video is blocked while Grok privacy mode is on: run `grok`, then /privacy, and opt in
    if any(k in t for k in ("moderat", "content policy", "not allowed", "violat", "unsafe")):
        return "moderation"
    if any(k in t for k in ("rate limit", "usage limit", "quota", "allowance", "too many requests")):
        return "allowance"
    return "unknown"


def absolute_args(args: dict) -> dict:
    out = dict(args)
    for k in IMAGE_KEYS:
        if k in out:
            p = (HERE / out[k]).resolve()
            if not p.is_file():
                raise SystemExit(f"missing input image {p}")
            out[k] = str(p)
    return out


def tool_calls(session: Path, tool: str) -> list[dict]:
    calls = []
    with (session / "updates.jsonl").open(encoding="utf-8") as f:
        for line in f:
            upd = json.loads(line).get("params", {}).get("update", {})
            if upd.get("sessionUpdate") == "tool_call" and upd.get("title") == tool:
                calls.append(upd.get("rawInput", {}))
    return calls


def call_grok(grok: str, mode: str, args: dict) -> tuple[Path, dict]:
    tool = TOOLS[mode]
    prompt = (f"Call {tool} exactly once with exactly these JSON arguments and nothing else:\n"
              f"{json.dumps(args, ensure_ascii=False)}\n"
              "Do not change, shorten, or improve any argument. Reply with only the returned absolute path.")
    runs = HERE / ".grok-runs"
    runs.mkdir(exist_ok=True)
    started = time.time()
    proc = subprocess.run([grok, "-p", prompt, "--tools", tool, "--max-turns", "3", "--always-approve",
                           "--output-format", "json", "--disable-web-search", "--no-subagents"],
                          cwd=runs, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT_S)
    meta = {"seconds": round(time.time() - started, 1)}
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"unknown: grok exited {proc.returncode} without JSON: {proc.stderr.strip()[-400:]}") from None
    text = result.get("text", "") or ""
    meta.update(session=result.get("sessionId"), agent_cost_usd=result.get("total_cost_usd"))
    matches = list(SESSIONS.glob(f"*/{meta['session']}")) if meta["session"] else []
    if len(matches) != 1:
        raise RuntimeError(f"unknown: expected one session folder for {meta['session']}, found {len(matches)}")
    session = matches[0]
    calls = tool_calls(session, tool)
    if not calls:
        raise RuntimeError(f"{classify(text)}: grok did not call {tool}: {text[-300:]}")
    if len(calls) != 1:
        raise RuntimeError(f"altered: grok called {tool} {len(calls)} times")
    if calls[0] != args:
        raise RuntimeError(f"altered: grok changed the arguments: used {json.dumps(calls[0])}")
    videos = sorted((session / "videos").glob("*")) if (session / "videos").is_dir() else []
    if len(videos) != 1:
        errors = " ".join(ln for ln in (session / "updates.jsonl").read_text(errors="ignore").splitlines() if '"error"' in ln)
        raise RuntimeError(f"{classify(text + errors)}: expected one video in {session / 'videos'}, found {len(videos)}")
    return videos[0], meta


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-only", action="store_true")
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    grok = os.environ.get("GROK_BIN") or shutil.which("grok")
    if not grok and not a.dry_run:
        raise SystemExit("grok is not on PATH; install it and sign in, or set GROK_BIN")
    reqs = json.loads((HERE / "requests.json").read_text(encoding="utf-8"))["requests"]
    todo = sorted(reqs, key=lambda r: (r["attempt"], r["shot"]))
    todo = [r for r in todo if not (HERE / r["clip"]).is_file()]
    if a.first_only:
        todo = [r for r in todo if r["attempt"] == 1]
    if a.only:
        todo = [r for r in todo if r["id"] in a.only]
    print(f"{len(todo)} call(s) to make", flush=True)
    last = 0.0
    for r in todo:
        args = absolute_args(r["args"])
        if a.dry_run:
            print(r["id"], TOOLS[r["mode"]], json.dumps(args, ensure_ascii=False))
            continue
        wait = MIN_GAP_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": r["id"], "fingerprint": r["fingerprint"], "sent": args}
        try:
            video, meta = call_grok(grok, r["mode"], args)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            last = time.time()
            msg = str(e)
            entry.update(status="failed", error=msg)
            with (HERE / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"{r['id']}: FAILED {msg}", flush=True)
            if msg.startswith("allowance"):
                print("allowance reached; stopping", flush=True)
                return 2
            continue
        last = time.time()
        dest = HERE / r["clip"]
        dest.parent.mkdir(exist_ok=True)
        tmp = dest.with_suffix(".part")
        shutil.copyfile(video, tmp)
        os.replace(tmp, dest)
        entry.update(status="ok", sha256=sha256(dest), **meta)
        with (HERE / "results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"{r['id']}: ok in {meta['seconds']} s -> {r['clip']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
