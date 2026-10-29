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


def _table(rows: list[tuple]) -> str:
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _follow_lab(store, shoot_id: str, lab_id: str, cursor: dict | None = None):
    """Print the lab's feed and phase changes as they happen; return the final state. `cursor` carries what was
    already printed across calls."""
    from .creative.lab import load_lab, wait_lab

    cur = cursor if cursor is not None else {}
    cur.setdefault("printed", 0)
    seen = cur.setdefault("seen", {})
    while True:
        finished = wait_lab(lab_id, 0.5)
        state = load_lab(store, shoot_id, lab_id)
        names = {a.id: a.name for a in state.agents}
        for p in state.phases:
            now = (p.status, p.detail)
            if seen.get(p.key, ("pending", "")) != now:
                timed = p.status == "done" and p.started is not None and p.ended is not None
                took = f" in {p.ended - p.started:.1f}s" if timed else ""
                print(f"== {p.label}: {p.status}{took}" + (f"  ({_short(p.detail, 120)})" if p.detail else ""), flush=True)
            seen[p.key] = now
        for m in state.feed[cur["printed"]:]:
            tag = f" [{m.concept}]" if m.concept else ""
            print(f"[{m.t:7.1f}s] {names.get(m.agent, m.agent):>18} {m.kind:<10}{tag} {_short(m.text, 220)}", flush=True)
        cur["printed"] = len(state.feed)
        if finished:
            return state


def _lab_summary(store, state) -> str:
    from .creative.lab import lab_dir

    ev = state.evaluation
    summaries = {s.reel: s for s in (ev.summaries if ev else [])}
    sims = {s.reel: s for s in state.simulations}
    hours = state.simulations[0].hours if state.simulations else state.options.hours
    rows: list[tuple] = [("reel", "label", "title", "creativity", "novelty", "pairwise", f"views@{hours}h", "shares",
                          "saves", "inquiries")]
    for r in state.reels:
        s, sim = summaries.get(r.id), sims.get(r.id)
        t = sim.totals if sim else None
        rows.append((r.id, r.label, _short(r.title, 34), f"{s.creativity:.1f} ±{s.creativity_sd:.1f}" if s else "-",
                     f"{s.novelty:.2f}" if s else "-", f"{s.pairwise_win_rate:.0%}" if s else "-",
                     f"{t.views:,}" if t else "-", f"{t.shares:,}" if t else "-", f"{t.saves:,}" if t else "-",
                     f"{t.inquiries:,}" if t else "-"))
    u = state.usage
    roles = ", ".join(f"{k} ${v:.2f}" for k, v in sorted(u.by_role.items(), key=lambda kv: -kv[1]))
    lines = ["", _table(rows) if state.reels else "(no reels rendered)", "",
             f"{state.status.upper()}  lab {state.id}: ${u.usd:.2f} for {u.calls} model calls ({roles or 'none'}), "
             f"{u.input_tokens:,} in / {u.output_tokens:,} out tokens, wall time {state.elapsed:.0f}s."]
    if summaries:
        best = max(summaries.values(), key=lambda s: s.creativity)
        lines.append(f"Most creative (judges): {best.reel}, {best.creativity:.1f}/10.")
    if sims:
        far = max(sims.values(), key=lambda s: s.totals.views)
        lines.append(f"Travels furthest (launch sim, {far.followers:,} followers): {far.reel}, "
                     f"{far.totals.views:,} views in {far.hours} h.")
    if ev and ev.verdict:
        lines += ["", "Verdict: " + ev.verdict]
    if state.error:
        lines.append(f"Error: {state.error}")
    if state.status in ("failed", "cancelled"):
        lines.append(f"Resume with: shootagent creative {state.shoot_id} --resume {state.id}")
    lines.append(f"Files: {lab_dir(store, state.shoot_id, state.id)}")
    return "\n".join(lines)


def cmd_creative(a) -> int:
    from .creative.lab import cancel_lab, lab_dir, start_lab, wait_lab
    from .creative.schema import LabOptions

    store = _store()
    store.load(a.shoot)
    picked = {"finalists": a.finalists, "rounds": a.rounds}
    opts = LabOptions(**{k: v for k, v in picked.items() if v is not None}, voice=not a.no_voice,
                      single_agent=not a.no_single_agent, render_single_agent=a.render_single_agent)
    lab_id = start_lab(store, a.shoot, opts, resume=a.resume)
    print(f"lab {lab_id}{' (resumed with its stored options)' if a.resume else ''}  "
          f"({lab_dir(store, a.shoot, lab_id) / 'state.json'})", flush=True)
    cursor: dict = {}
    try:
        state = _follow_lab(store, a.shoot, lab_id, cursor)
    except KeyboardInterrupt:
        print("\ncancelling (Ctrl-C again to quit at once) ...", flush=True)
        cancel_lab(store, a.shoot, lab_id)
        wait_lab(lab_id, 120)
        state = _follow_lab(store, a.shoot, lab_id, cursor)
    print(_lab_summary(store, state))
    return 0 if state.status == "done" else 1


def cmd_creative_sim(a) -> int:
    from .creative.lab import load_lab, resimulate

    store = _store()
    o = load_lab(store, a.shoot, a.lab).options
    sims = resimulate(store, a.shoot, a.lab, followers=o.followers if a.followers is None else a.followers,
                      hours=o.hours if a.hours is None else a.hours, seed=o.seed if a.seed is None else a.seed)
    state = load_lab(store, a.shoot, a.lab)
    labels = {r.id: r.label for r in state.reels}
    rows: list[tuple] = [("reel", "label", "views", "likes", "comments", "shares", "saves", "follows", "inquiries")]
    for s in sims:
        t = s.totals
        rows.append((s.reel, labels.get(s.reel, ""), f"{t.views:,}", f"{t.likes:,}", f"{t.comments:,}",
                     f"{t.shares:,}", f"{t.saves:,}", f"{t.follows:,}", f"{t.inquiries:,}"))
    first = sims[0] if sims else None
    print(f"launch sim: {first.followers:,} followers, {first.hours} h, seed {first.seed} (saved to lab {a.lab})"
          if first else "nothing to simulate")
    print(_table(rows))
    for s in sims:
        waves = next((n for n in s.notes if n.startswith("Waves")), "")
        top = max(s.comments, key=lambda c: c.likes, default=None)
        print(f"\n{s.reel}: {waves}" + (f"\n   top comment ({top.name}, {top.likes} likes): {top.text!r}" if top else ""))
    if first:
        print("\nAssumptions:\n" + "\n".join(f"  - {n}" for n in first.notes if not n.startswith(("Waves", "No "))))
    return 0


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

    s = sub.add_parser("creative", help="run the creative lab on a shoot: the room, the renders, the audience and "
                                        "judges, and a simulated launch, with the room's feed printed live")
    s.add_argument("shoot")
    s.add_argument("--finalists", type=int, metavar="N", help="creative reels to render besides the template (default 2)")
    s.add_argument("--rounds", type=int, metavar="N", help="1 = pitch only, 2 = pitch, critique, remix (default 2)")
    s.add_argument("--no-voice", action="store_true", help="render silent, music-only reels")
    s.add_argument("--no-single-agent", action="store_true", help="skip the single-agent control")
    s.add_argument("--render-single-agent", action="store_true", help="also render the single agent's pick as S")
    s.add_argument("--resume", metavar="LAB", help="continue a failed or cancelled lab, with its stored options")
    s.set_defaults(fn=cmd_creative)

    s = sub.add_parser("creative-sim", help="re-run a lab's simulated launch with other parameters and save it")
    s.add_argument("shoot")
    s.add_argument("lab")
    s.add_argument("--followers", type=int, metavar="N", help="default: the lab's current value (8000)")
    s.add_argument("--hours", type=int, metavar="N", help="default: the lab's current value (48)")
    s.add_argument("--seed", type=int, metavar="N", help="default: the lab's current value (7)")
    s.set_defaults(fn=cmd_creative_sim)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
