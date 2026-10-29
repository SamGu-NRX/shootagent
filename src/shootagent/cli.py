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
    if k == "check" and "clip" in d:
        verdict = "PASS" if d.get("passed") else "FAIL"
        lines = [f"    ◆ clip check {tag}shot {d.get('shot')} attempt {d.get('attempt')} ({d.get('clip')}): {verdict}"]
        lines += [f"        - {_short(r, 160)}" for r in d.get("failures") or []]
        return "\n".join(lines)
    if k == "media":
        what = f"shot {d['shot']}" if d.get("shot") is not None else f"narration {d.get('language')}"
        how = "cache" if d.get("cached") else f"{d.get('seconds')} s"
        return f"    ▸ {tag}{what}: {d.get('backend')} {d.get('fingerprint')} ({how})"
    if k == "shot":
        fell = " (fell back)" if d.get("fell_back") else ""
        return f"    ■ {tag}shot {d.get('shot')} plays {d.get('source')}{fell}: {d.get('note') or ''}"
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


def cmd_film(a) -> int:
    return _run(a.shoot, "film", views=a.views, languages=a.languages)


def cmd_launch(a) -> int:
    """QC on every view QC has not checked, approval of the views QC left clean, then the film."""
    from .film.readiness import review_state

    store = _store()
    state = review_state(store, store.load(a.shoot))
    need = [v for v, s in state.items() if s == "needs_qc"]
    if need:
        print(f"QC first on {', '.join(need)}")
        if _run(a.shoot, "qc", views=need) != 0:
            print("QC did not finish; the film was not made.")
            return 1
    shoot = store.load(a.shoot)
    state = review_state(store, shoot)
    clean = [v for v, s in state.items() if s == "qc_ok"]  # never "not_deliverable": QC advised against those
    held = [v for v, s in state.items() if s in ("open_findings", "needs_qc")]
    advised = [v for v, s in state.items() if s == "not_deliverable"]

    def approve(s):
        for v in clean:
            s.view(v).approved = True

    store.mutate(a.shoot, approve)
    print(f"approved {', '.join(clean) or 'none'}; already approved "
          f"{', '.join(v for v, s in state.items() if s == 'approved') or 'none'}; left out "
          f"{', '.join(held) or 'none'} (open or failed findings) and {', '.join(advised) or 'none'} (QC advised "
          f"against delivering); split into parts: "
          f"{', '.join(v for v, s in state.items() if s == 'split') or 'none'}")
    return _run(a.shoot, "film", languages=a.languages)


def cmd_film_export(a) -> int:
    from .film.handoff import export_requests
    from .film.spec import FilmSpec, Shot
    from .tools.film import PlanFilmIn

    settings = get_settings()
    store = _store()
    shoot = store.load(a.shoot)
    spec = FilmSpec.load(store.shoot_dir(a.shoot) / "film" / "spec.json")
    if a.plan:
        plan = PlanFilmIn.model_validate_json(Path(a.plan).read_text(encoding="utf-8"))
        shots = [Shot(**{**s.model_dump(), "end_label": s.end_label or ""}) for s in plan.shots]
    elif spec is not None:
        shots = spec.shots
    else:
        print("No film plan: pass --plan plan.json or plan a film first.", file=sys.stderr)
        return 1
    # Narrations come from the film spec's scripts (`shootagent film-script`), one TTS request per language.
    from .film.narration import speech_request

    speech = [] if a.no_tts or spec is None else [
        speech_request(settings, lang, spec.narrations[lang].text) for lang in spec.languages if lang in spec.narrations]
    missing = [v for s in shots for v in (s.view, s.end_view) if v and (v not in {x.id for x in shoot.views}
                                                                        or shoot.view(v).head is None)]
    if missing:
        print(f"Unknown or empty views in the plan: {', '.join(missing)}", file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else settings.data_dir / "handoff" / a.shoot
    entries = export_requests(store, a.shoot, shots, out, attempts=a.attempts, speech=speech)
    if a.plan:
        (out / "plan.json").write_text(Path(a.plan).read_text(encoding="utf-8"), encoding="utf-8")
    for e in entries:
        if e["kind"] == "speech":
            print(f"{e['id']:<7} {e['fingerprint']}  tts   {e['request']['text'][:80]}")
        else:
            print(f"{e['id']}  {e['fingerprint']}  {e['mode']:<5} {e['view']}{' -> ' + e['end_view'] if e['end_view'] else ''}")
    print(f"{len(entries)} request(s) in {out}")
    return 0


def cmd_film_script(a) -> int:
    """Handoff step 1: the plan from a file, then a film_script agent run for the words. Export comes next."""
    from .film.readiness import film_scope
    from .tools import ToolContext, run_tool
    from .tools.film import PlanFilmIn, start_film
    from .trace import NullTrace

    store = _store()
    plan = PlanFilmIn.model_validate_json(Path(a.plan).read_text(encoding="utf-8"))
    views = list(dict.fromkeys(v for s in plan.shots for v in (s.view, s.end_view) if v))
    film_scope(store, store.load(a.shoot), views)  # approved photos only; raises with what to do
    ctx = ToolContext(store, a.shoot, NullTrace(), get_settings())
    start_film(ctx, views, a.languages)
    res = run_tool(ctx, "plan_film", plan.model_dump(exclude_none=True))
    if not res.ok:
        print(f"The plan was refused: {res.summary}", file=sys.stderr)
        return 1
    print(res.summary)
    return _run(a.shoot, "film_script", views=views, languages=a.languages)


def cmd_film_finish(a) -> int:
    """Handoff step 3, after film-import-media: prepare every shot, narrate and assemble from film/spec.json.
    Deterministic code only; the trace is a film run the UI shows like any other."""
    import time

    from .tools import ToolContext, run_tool
    from .tools.film import load_spec
    from .trace import TraceRun

    store, settings = _store(), get_settings()
    trace = TraceRun(store.shoot_dir(a.shoot), "film")
    store.mutate(a.shoot, lambda s: s.runs.append(trace.id))
    ctx = ToolContext(store, a.shoot, trace, settings)
    spec = load_spec(ctx)
    trace.emit("run_start", mode="film", request="film-finish: prepare, narrate and assemble the planned film",
               views=list(dict.fromkeys(s.view for s in spec.shots)), languages=spec.languages, media=settings.film_media)
    ok, summary, started = True, "", time.perf_counter()
    for name, inp in (("prepare_shots", {"shots": list(range(len(spec.shots)))}), ("narrate", {}), ("assemble_film", {})):
        res = run_tool(ctx, name, inp)
        print(f"{'ok' if res.ok else 'FAILED'} {name}: {res.summary}", flush=True)
        summary = res.summary
        if not res.ok:
            ok = False
            break
    trace.emit("run_end", ok=ok, summary=summary[:600], ms=int((time.perf_counter() - started) * 1000),
               usd=round(trace.usd, 4), counts={})
    print(f"run {trace.id}: {'ok' if ok else 'failed'}, ${trace.usd:.4f}")
    return 0 if ok else 1


def cmd_film_import(a) -> int:
    from .film.handoff import import_media

    r = import_media(get_settings(), Path(a.folder))
    for x in r["imported"]:
        print(f"imported {x['id']} {x['fingerprint']} ({x['seconds']} s)")
    for rid, why in r["skipped"]:
        print(f"skipped  {rid}: {why}")
    return 0 if r["imported"] or not r["skipped"] else 1


SAMPLE_LISTING = Path(__file__).resolve().parents[2] / "web" / "src" / "listing" / "sample-listing.json"


def cmd_listing_sample(a) -> int:
    """Write the demo's sample listing (web/src/listing/sample-listing.json, the one copy the UI also reads)."""
    from .store import Listing

    sample = Listing.model_validate_json(SAMPLE_LISTING.read_text(encoding="utf-8"))
    shoot = _store().mutate(a.shoot, lambda s: setattr(s, "listing", sample))
    print(f"{shoot.id}: {shoot.listing.model_dump_json(exclude_none=True)}")
    return 0


def cmd_film_seed(a) -> int:
    from .film.seed import seed_replay

    settings = get_settings()
    for e in seed_replay(settings, branch=a.branch):
        print(f"{e['fingerprint']}  {e['request']['kind']:<6} {e['file']}")
    print(f"replay index: {settings.replay_dir / 'index.json'}")
    return 0


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

    s = sub.add_parser("film", help="make the narrated listing film and print the trace")
    s.add_argument("shoot")
    s.add_argument("--languages", nargs="+", default=["en"], help="narration languages, script language first")
    s.add_argument("--views", nargs="+")
    s.set_defaults(fn=cmd_film)

    s = sub.add_parser("launch", help="QC what QC has not checked, approve the clean views, then make the film")
    s.add_argument("shoot")
    s.add_argument("--languages", nargs="+", default=["en"], help="narration languages, script language first")
    s.set_defaults(fn=cmd_launch)

    s = sub.add_parser("film-export-requests", help="write a shoot's planned grok requests and a runner for another machine")
    s.add_argument("shoot")
    s.add_argument("--plan", help="JSON {shots: [...]} in plan_film's shape; default the shoot's current film plan")
    s.add_argument("--out", help="folder to write; default <data>/handoff/<shoot>")
    s.add_argument("--attempts", type=int, default=2, choices=(1, 2))
    s.add_argument("--no-tts", action="store_true", help="leave out the narration requests")
    s.set_defaults(fn=cmd_film_export)

    s = sub.add_parser("film-script", help="set a film's shot plan and have the agent write and translate its script")
    s.add_argument("shoot")
    s.add_argument("--plan", required=True, help="JSON {shots: [...]} in plan_film's shape")
    s.add_argument("--languages", nargs="+", default=["en"])
    s.set_defaults(fn=cmd_film_script)

    s = sub.add_parser("film-finish", help="make the clips, narrate and assemble a film from its spec, with no agent")
    s.add_argument("shoot")
    s.set_defaults(fn=cmd_film_finish)

    s = sub.add_parser("film-import-media", help="verify the clips a handoff folder came back with and add them to replay")
    s.add_argument("folder")
    s.set_defaults(fn=cmd_film_import)

    s = sub.add_parser("listing-sample", help="write the demo's sample listing (no street address) to a shoot")
    s.add_argument("shoot")
    s.set_defaults(fn=cmd_listing_sample)

    s = sub.add_parser("film-seed-replay", help="copy the recorded Grok clips and Gemini narrations into the replay folder")
    s.add_argument("--branch", default="origin/explore/media-tests")
    s.set_defaults(fn=cmd_film_seed)

    s = sub.add_parser("runs", help="list a shoot's agent runs")
    s.add_argument("shoot")
    s.add_argument("-v", "--verbose", action="store_true", help="print each run's trace")
    s.set_defaults(fn=cmd_runs)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
