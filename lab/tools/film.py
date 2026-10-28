#!/usr/bin/env python3
"""Generate film shots through the Grok Build CLI on a subscription login.

Each request is identified by a fingerprint of its kind, arguments and the
bytes of any input images. A finished fingerprint is never generated again,
so a crash or a rerun of the whole shot list costs nothing for shots already
on disk. Calls run one at a time (a file lock) with a minimum gap between
them, because the subscription allowance is shared and bursts buy nothing.

Grok's headless mode puts a model between us and the Imagine tool, so after
every call we read the session's own event log and check that the tool was
called once with exactly our arguments. A mismatch is an error, not a
warning: an off-prompt shot that silently lands in the edit is worse than a
failed one.

Usage:
  film.py image --id S01 --prompt "..." [--aspect 16:9]
  film.py edit  --id S01b --prompt "..." --image a.jpg [--image b.jpg] [--aspect 16:9]
  film.py video --id S01v --image S01.jpg --prompt "..." [--duration 6] [--res 720p]
  film.py ref   --id S07v --prompt "..." --aspect 16:9 [--first-frame f.jpg]
                [--last-frame l.jpg] [--ref-image r.jpg ...] [--duration 6] [--res 720p]
  film.py log   [--last 20]

Outputs go to <project>/shots/<id>/<kind>-<fingerprint>.<ext>; every call
is appended to <project>/shots/log.jsonl.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SHOTS = PROJECT / "shots"
RUNS = PROJECT / ".grok-runs"  # grok's working dir; keeps its sessions grouped
LOG = SHOTS / "log.jsonl"
STATE = Path.home() / ".cache" / "film"
LOCK = STATE / "grok.lock"
LAST_CALL = STATE / "grok.last"
SESSIONS = Path.home() / ".grok" / "sessions"

# No evidence yet for how fast the allowance tolerates calls; 20 s keeps us
# far from anything burst-like while a 6 s clip already takes ~40 s to make.
MIN_GAP_S = 20
GROK_TIMEOUT_S = 900

TOOLS = {"image": "image_gen", "edit": "image_edit", "video": "image_to_video", "ref": "reference_to_video"}
EXT = {"image": "jpg", "edit": "jpg", "video": "mp4", "ref": "mp4"}


class FilmError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def abs_existing(p: str) -> str:
    path = Path(p).expanduser().resolve()
    if not path.is_file():
        raise FilmError(f"input image not found: {p}")
    return str(path)


def build_tool_args(kind: str, a: argparse.Namespace) -> dict:
    if kind == "image":
        return {"prompt": a.prompt, "aspect_ratio": a.aspect}
    if kind == "edit":
        return {"prompt": a.prompt, "image": [abs_existing(p) for p in a.image], "aspect_ratio": a.aspect}
    if kind == "video":
        return {"image": abs_existing(a.image), "prompt": a.prompt, "duration": a.duration,
                "resolution_name": a.res}
    if kind == "ref":
        args = {"prompt": a.prompt, "aspect_ratio": a.aspect, "duration": a.duration, "resolution_name": a.res}
        if a.first_frame:
            args["first_frame"] = abs_existing(a.first_frame)
        if a.last_frame:
            args["last_frame"] = abs_existing(a.last_frame)
        if a.ref_image:
            args["images"] = [abs_existing(p) for p in a.ref_image]
        if not (a.first_frame or a.last_frame or a.ref_image):
            raise FilmError("ref needs at least one of --first-frame, --last-frame, --ref-image")
        return args
    raise FilmError(f"unknown kind {kind}")


def input_files(args: dict) -> list[str]:
    files = []
    for key in ("image", "images", "first_frame", "last_frame"):
        v = args.get(key)
        if isinstance(v, str):
            files.append(v)
        elif isinstance(v, list):
            files.extend(v)
    return files


def fingerprint(kind: str, args: dict) -> str:
    # Input images count by content, so regenerating an upstream still
    # invalidates every clip animated from it.
    payload = {"kind": kind, "args": args, "inputs": {p: sha256_file(Path(p)) for p in input_files(args)}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def append_log(entry: dict) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def wait_for_gap() -> None:
    if LAST_CALL.exists():
        elapsed = time.time() - LAST_CALL.stat().st_mtime
        if elapsed < MIN_GAP_S:
            time.sleep(MIN_GAP_S - elapsed)


def find_session_dir(session_id: str) -> Path:
    matches = list(SESSIONS.glob(f"*/{session_id}"))
    if len(matches) != 1:
        raise FilmError(f"expected one grok session dir for {session_id}, found {len(matches)}")
    return matches[0]


def tool_calls(session_dir: Path, tool: str) -> list[dict]:
    """Return the rawInput of every initial call to `tool` in the session."""
    calls = []
    with (session_dir / "updates.jsonl").open() as f:
        for line in f:
            upd = json.loads(line).get("params", {}).get("update", {})
            if upd.get("sessionUpdate") == "tool_call" and upd.get("title") == tool:
                calls.append(upd.get("rawInput", {}))
    return calls


def find_output(session_dir: Path, kind: str) -> Path:
    sub = "videos" if EXT[kind] == "mp4" else "images"
    files = sorted((session_dir / sub).glob("*")) if (session_dir / sub).is_dir() else []
    if len(files) != 1:
        raise FilmError(f"expected exactly one file in {session_dir / sub}, found {len(files)}")
    return files[0]


def classify_failure(text: str) -> str:
    t = text.lower()
    if "zero data retention" in t or "zdr" in t:
        return "zdr: video is blocked while Grok privacy mode is on; run `grok`, then /privacy, and opt in"
    if any(k in t for k in ("moderat", "content policy", "not allowed", "violat", "unsafe")):
        return "moderation: Imagine refused this prompt; rewrite it rather than retrying"
    if any(k in t for k in ("rate limit", "usage limit", "quota", "allowance", "too many requests")):
        return "allowance: the subscription limit was hit; check /usage in grok and resume later"
    return "unknown"


def run_grok(kind: str, args: dict) -> tuple[Path, dict]:
    tool = TOOLS[kind]
    prompt = (
        f"Call {tool} exactly once with exactly these JSON arguments and nothing else:\n"
        f"{json.dumps(args, ensure_ascii=False)}\n"
        "Do not change, shorten, or improve any argument. Reply with only the returned absolute path."
    )
    RUNS.mkdir(parents=True, exist_ok=True)
    cmd = ["grok", "-p", prompt, "--tools", tool, "--max-turns", "3", "--always-approve",
           "--output-format", "json", "--disable-web-search", "--no-subagents"]
    started = time.time()
    proc = subprocess.run(cmd, cwd=RUNS, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=GROK_TIMEOUT_S)
    LAST_CALL.touch()
    seconds = round(time.time() - started, 1)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise FilmError(f"grok exited {proc.returncode} without JSON output: {proc.stderr.strip()[-500:]}")
    meta = {"session": result.get("sessionId"), "seconds": seconds,
            "agent_cost_usd": result.get("total_cost_usd"), "grok_text": result.get("text", "")[-400:]}

    session_dir = find_session_dir(result["sessionId"])
    calls = tool_calls(session_dir, tool)
    if not calls:
        raise FilmError(f"grok did not call {tool} ({classify_failure(result.get('text', ''))}): "
                        f"{meta['grok_text']}")
    if len(calls) != 1:
        raise FilmError(f"grok called {tool} {len(calls)} times; expected once (session {meta['session']})")
    if calls[0] != args:
        raise FilmError(f"grok altered the arguments.\n sent: {json.dumps(args)}\n used: {json.dumps(calls[0])}")
    try:
        out = find_output(session_dir, kind)
    except FilmError as e:
        reason = classify_failure(json.dumps(calls) + result.get("text", "") + _tool_errors(session_dir))
        raise FilmError(f"{e} ({reason}): {meta['grok_text']}")
    return out, meta


def _tool_errors(session_dir: Path) -> str:
    text = (session_dir / "updates.jsonl").read_text(errors="ignore")
    return " ".join(line for line in text.splitlines() if '"error"' in line)[-2000:]


def generate(kind: str, shot_id: str, args: dict) -> Path:
    fp = fingerprint(kind, args)
    dest = SHOTS / shot_id / f"{kind}-{fp}.{EXT[kind]}"
    if dest.exists():
        print(dest)
        return dest

    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if dest.exists():  # another process finished it while we waited
            print(dest)
            return dest
        wait_for_gap()
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": shot_id, "kind": kind, "fp": fp, "args": args}
        try:
            out, meta = run_grok(kind, args)
        except (FilmError, subprocess.TimeoutExpired) as e:
            append_log({**entry, "status": "failed", "error": str(e)})
            raise FilmError(str(e)) from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(out, tmp)
        os.replace(tmp, dest)
        append_log({**entry, "status": "ok", "file": str(dest.relative_to(PROJECT)), **meta})
    print(dest)
    return dest


def show_log(last: int) -> None:
    if not LOG.exists():
        print("no calls yet")
        return
    rows = [json.loads(l) for l in LOG.read_text().splitlines()][-last:]
    for r in rows:
        print(f"{r['t']}  {r['status']:6}  {r['kind']:5}  {r['id']:10}  {r.get('seconds', '-'):>6}s  "
              f"{r.get('file') or r.get('error', '')[:90]}")
    ok = [r for r in json.loads("[" + ",".join(LOG.read_text().splitlines()) + "]") if r["status"] == "ok"]
    print(f"\ntotal ok: {sum(r['kind'] in ('video', 'ref') for r in ok)} clips, "
          f"{sum(r['kind'] in ('image', 'edit') for r in ok)} images")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="kind", required=True)

    def common(sp):
        sp.add_argument("--id", required=True, help="shot id, e.g. S01; names the output folder")
        sp.add_argument("--prompt", required=True)

    sp = sub.add_parser("image"); common(sp); sp.add_argument("--aspect", default="16:9")
    sp = sub.add_parser("edit"); common(sp)
    sp.add_argument("--image", action="append", required=True); sp.add_argument("--aspect", default="auto")
    for name in ("video", "ref"):
        sp = sub.add_parser(name); common(sp)
        sp.add_argument("--duration", type=int, choices=(6, 10), default=6)
        sp.add_argument("--res", choices=("480p", "720p"), default="720p")
        if name == "video":
            sp.add_argument("--image", required=True)
        else:
            sp.add_argument("--aspect", required=True)
            sp.add_argument("--first-frame"); sp.add_argument("--last-frame")
            sp.add_argument("--ref-image", action="append")
    sp = sub.add_parser("log"); sp.add_argument("--last", type=int, default=20)

    a = p.parse_args()
    if a.kind == "log":
        show_log(a.last)
        return 0
    try:
        generate(a.kind, a.id, build_tool_args(a.kind, a))
    except FilmError as e:
        print(f"film: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
