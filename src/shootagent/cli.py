"""`shootagent` command line: serve the API, build shoots, and run the agent with a readable live trace."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import get_settings
from .store import ShootStore


def _store() -> ShootStore:
    return ShootStore(get_settings().shoots_dir)


def _short(value, n: int = 140) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def format_event(ev: dict) -> str | None:
    """One readable line (or a few) per trace event; None to skip."""
    k, d, view = ev["kind"], ev["data"], ev.get("view")
    tag = f"[{view}] " if view else ""
    if k == "run_start":
        return f"▶ {d['mode']} run on {', '.join(d.get('views') or [])}" + (
            f": {d['request']!r}" if d.get("request") else ""
        )
    if k == "plan":
        lines = ["PLAN " + (d.get("text") or "")]
        lines += [
            f"   {i}. "
            + (f"[{s['view']}] " if s.get("view") else "")
            + s["intent"]
            + (f"  ({s['tool']})" if s.get("tool") else "")
            for i, s in enumerate(d.get("steps", []), 1)
        ]
        return "\n".join(lines)
    if k == "thought":
        return f"  … {d['text']}"
    if k == "tool_call":
        args = {a: b for a, b in (d.get("input") or {}).items() if a not in ("view",)}
        return f"  → {tag}{d['tool']}({_short(args, 120)})"
    if k == "tool_result":
        mark = "✓" if d.get("ok") else "✗"
        extra = f" → {d['version']}" if d.get("version") else ""
        return f"    {mark} {d.get('summary')}{extra} ({d.get('ms')} ms)"
    if k == "check":
        j = d.get("judge") or {}
        verdict = "PASS" if d.get("passed") else "FAIL"
        m = ", ".join(f"{a}={b}" for a, b in (d.get("metrics") or {}).items())
        reason = f" judge: {j.get('verdict')} - {j.get('reason')}" if j else ""
        return f"    ◆ check {tag}{d.get('version')}: {verdict} [{m}]{reason}"
    if k == "retry":
        return f"    ↻ {tag}retry {d.get('attempt')}/{d.get('max_attempts')}: {d.get('reason')} → {d.get('next')}"
    if k == "finding":
        f = d["finding"]
        return f"    ● {tag}finding {f['id']} {f['type']} [{f['status']}] {_short(f.get('explanation', ''), 100)}"
    if k == "message":
        return f"\n{d['text']}\n"
    if k == "error":
        return f"  ! error: {d['message']}"
    if k == "run_end":
        c = d.get("counts") or {}
        return (
            f"■ {'ok' if d.get('ok') else 'failed'} in {d.get('ms', 0) / 1000:.1f}s, ${d.get('usd', 0):.4f}; "
            + ", ".join(f"{a}={b}" for a, b in c.items())
        )
    return None


async def _follow(trace) -> dict | None:
    q = trace.subscribe()
    try:
        while True:
            ev = await q.get()
            line = format_event(ev)
            if line:
                print(line, flush=True)
            if ev["kind"] == "run_end":
                return ev
    finally:
        trace.unsubscribe(q)


def _run(shoot_id: str, mode: str, **kw) -> int:
    from .agent.loop import start_run

    store = _store()
    store.load(shoot_id)
    trace = start_run(store, shoot_id, mode, **kw)
    print(f"run {trace.id}  (trace: {trace.path})")
    end = asyncio.run(_follow(trace))
    return 0 if end and end["data"].get("ok") else 1


def cmd_serve(a) -> int:
    import uvicorn

    uvicorn.run("shootagent.api.app:create_app", factory=True, host=a.host, port=a.port, reload=a.reload)
    return 0


def cmd_ingest(a) -> int:
    from . import ingest

    shoot = ingest.create_shoot_from_defect_cases(_store(), a.title, [Path(p) for p in a.cases])
    print(shoot.id if hasattr(shoot, "id") else shoot)
    return 0


def cmd_demo(a) -> int:
    from . import ingest

    shoot = ingest.build_demo_shoot(_store(), a.preset)
    sid = shoot.id if hasattr(shoot, "id") else str(shoot)
    s = _store().load(sid)
    print(f"{sid}  {s.title}  ({len(s.views)} views: {', '.join(v.id for v in s.views)})")
    return 0


def cmd_qc(a) -> int:
    return _run(a.shoot, "qc", views=a.views)


def cmd_chat(a) -> int:
    return _run(a.shoot, "chat", request=a.message, views=a.views, mask=a.mask)


def cmd_runs(a) -> int:
    from .trace import read_events

    store = _store()
    shoot = store.load(a.shoot)
    for rid in shoot.runs:
        events = read_events(store.shoot_dir(a.shoot) / "runs" / f"{rid}.jsonl")
        start = next((e for e in events if e["kind"] == "run_start"), None)
        end = next((e for e in reversed(events) if e["kind"] == "run_end"), None)
        mode = start["data"]["mode"] if start else "?"
        if end:
            d = end["data"]
            state = f"{'ok' if d.get('ok') else 'failed'} {d.get('ms', 0) / 1000:.1f}s ${d.get('usd', 0):.4f}"
        else:
            state = "running/unfinished"
        req = f" {start['data']['request']!r}" if start and start["data"].get("request") else ""
        print(f"{rid}  {mode:<4} {state}{req}")
        if a.verbose:
            for ev in events:
                line = format_event(ev)
                if line:
                    print("    " + line.replace("\n", "\n    "))
    return 0


def cmd_publish(a) -> int:
    from .compliance.publish import public_url, publish

    pub = publish(_store(), a.shoot)
    for ph in pub.photos:
        print(f"{ph.order:>2}  {ph.label:<28} {ph.status:<10} {ph.disclosure or ''}")
    print(f"published v{pub.version}  root {pub.root[:16]}  {public_url(a.base_url, pub.token)}")
    return 0


def cmd_kit(a) -> int:
    from .compliance.kit import build_kit

    info = build_kit(_store(), a.shoot, a.base_url)
    print(f"{info['path']}  ({info['size'] / 1e6:.1f} MB, {info['photos']} photos, {info['altered']} altered)")
    return 0


def cmd_verify(a) -> int:
    from .compliance.kit import verify_kit

    res = verify_kit(Path(a.kit))
    for ph in res["photos"]:
        mark = "ok " if ph["ok"] else "BAD"
        print(f"{mark} {ph['order']:>2}  {ph['label']}")
        for problem in ph["problems"]:
            print(f"       {problem}")
    print(("verified" if res["ok"] else "NOT verified") + f"  root {str(res.get('root') or '')[:16]}")
    return 0 if res["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="shootagent")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the HTTP API (and web/dist if built)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("ingest-autohdr", help="create a shoot from AutoHDR defect case directories")
    s.add_argument("cases", nargs="+")
    s.add_argument("--title", default="AutoHDR cases")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("demo", help="build a demo shoot from the datasets on disk")
    s.add_argument("preset", help="showcase, qc-tour, chat or requests")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("qc", help="run the autonomous QC pass and print the trace")
    s.add_argument("shoot")
    s.add_argument("--views", nargs="+")
    s.set_defaults(fn=cmd_qc)

    s = sub.add_parser("chat", help="apply a chat request across the shoot and print the trace")
    s.add_argument("shoot")
    s.add_argument("message")
    s.add_argument("--views", nargs="+")
    s.add_argument("--mask")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("runs", help="list a shoot's agent runs")
    s.add_argument("shoot")
    s.add_argument("-v", "--verbose", action="store_true", help="print each run's trace")
    s.set_defaults(fn=cmd_runs)

    s = sub.add_parser("publish", help="publish the listing photos with disclosures and provenance")
    s.add_argument("shoot")
    s.add_argument("--base-url", default="http://127.0.0.1:8000")
    s.set_defaults(fn=cmd_publish)

    s = sub.add_parser("kit", help="build the listing kit zip")
    s.add_argument("shoot")
    s.add_argument("--base-url", default="http://127.0.0.1:8000")
    s.set_defaults(fn=cmd_kit)

    s = sub.add_parser("verify", help="check a listing kit's photos against its provenance chains")
    s.add_argument("kit", help="path to a listing kit zip")
    s.set_defaults(fn=cmd_verify)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
