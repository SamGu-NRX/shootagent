# HTTP API

FastAPI app in `src/shootagent/api/`, served by `shootagent serve` on port 8000. The web UI's Vite dev
server proxies `/api` to it. JSON everywhere except blob and reel downloads. Errors are
`{"error": {"code": "...", "message": "plain sentence saying what to do"}}` with a 4xx/5xx status.
If `DEMO_PASSCODE` is set, every `/api` request needs the passcode: header `x-demo-passcode`, or query
`?passcode=` / cookie `demo_passcode` for requests that cannot set headers (EventSource, `<img>`).
Wrong or missing: 401 `passcode_required`. The static UI at `/` is not guarded.

## Shoots

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/shoots` | | `[{id, title, created_at, view_count, thumb}]` |
| POST | `/api/shoots` | multipart: `title`, files named `view<N>_bracket<M>` and `view<N>_finished` | `Shoot` |
| POST | `/api/shoots/demo` | `{preset: "qc-tour" \| "chat" \| ...}` | `Shoot` (built from the AutoHDR datasets on disk) |
| GET | `/api/shoots/{id}` | | `Shoot` (store.Shoot JSON, `schema` key) |
| PATCH | `/api/shoots/{id}/listing` | `Listing` fields | `Shoot` |
| GET | `/api/shoots/{id}/blobs/{blob}` | `?w=<px>` long edge, optional | image bytes; resized copies cached; immutable cache headers |

## Views and versions

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/shoots/{id}/views/{view}/head` | `{version}` | `View` (undo/redo/compare by moving head) |
| POST | `/api/shoots/{id}/views/{view}/approve` | `{approved: bool}` | `View` |
| POST | `/api/shoots/{id}/views/{view}/masks` | `{points: [[x,y,label]], box?: [x0,y0,x1,y1]}` normalised; label 1 = object, 0 = background; a box alone also works | `Mask` (SAM 2.1 from user clicks) |

## Agent runs

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/shoots/{id}/qc` | `{views?: [id]}` | `{run_id}` autonomous QC pass |
| POST | `/api/shoots/{id}/chat` | `{message, views?: [id], mask?: id}` | `{run_id}` chat request |
| POST | `/api/shoots/{id}/reel` | `{style?: {...}}` | `{run_id}` reel run: the agent labels rooms, orders approved views (all views when none are approved), renders, and reviews the frames |
| GET | `/api/shoots/{id}/runs` | | `[{id, mode, request, started, done, status, usd, summary}]`; `status` is `queued`, `running`, `done`, `cancelled`, or `unfinished` (no `run_end` and not live) |
| GET | `/api/shoots/{id}/runs/{run}/events` | | SSE stream: one `event: trace` per trace event (trace.py shape, SSE `id` = `seq`), replaying past events first; closes after `run_end`. Honors `Last-Event-ID`. A run with no `run_end` that is not live (server restarted) ends with a synthetic `run_end` `{ok: false}` so EventSource stops reconnecting. A queued run's stream stays open and starts at its `run_start`. |
| POST | `/api/shoots/{id}/runs/{run}/cancel` | | `{ok}`; `ok: false` if the run already ended. A queued run ends at once with `run_end {ok: false}`; a running one stops at its next step. |
| GET | `/api/shoots/{id}/reel/latest.mp4` | | video |

A chat message while a run is active on the same shoot is queued, not run in parallel.

## Trace event shape

```json
{"run": "20261028-031500-ab12cd", "seq": 7, "t": "2026-10-28T03:15:02.120+00:00",
 "kind": "tool_result", "view": "v03",
 "data": {"call_id": "toolu_..", "tool": "restore_from_brackets", "ok": true,
          "summary": "Restored the lamp the edit had erased (4.1% of the frame).",
          "version": "v03.2", "mask": "m_1a2b3c4d", "images": ["9f..png"], "ms": 812}}
```

`check` events: `{call_id, version, parent, passed, intent, failures: [str], metrics: {outside_max_delta,
mask_area, ev_target, ev_measured, delta_e_after, region_ssim_to_reference, ...}, judge: {verdict:
"pass"|"fail"|"unavailable", reason, issues, checks, model, overridden?}}`. A failed check has already moved
the view's head back to `parent`. `retry` events: `{attempt, max_attempts, reason, next}` (none after the
last allowed attempt). `finding` events: `{finding, images?}` (store.Finding; re-emitted when its status
changes). `usage` events: `{purpose: "agent"|"judge_edit"|"generative_edit", model, input_tokens,
output_tokens, cache_read_input_tokens, cache_creation_input_tokens, usd}`. `run_end`: `{ok, summary, ms,
usd, counts: {findings_detected, findings_fixed, findings_dismissed, findings_failed, edits_applied,
edits_reverted, edits_skipped, tool_calls, refused_calls, model_calls}}`; `findings_detected` counts
candidates the agent confirmed as real (not dismissed). A tool call the loop refuses (no plan yet, view
outside the run, attempt budget spent) still appears as `tool_call` + `tool_result {ok: false, data:
{refused: true}}`.

## Creative lab

A lab (`src/shootagent/creative/lab.py`) runs on a worker thread: a writers' room of agents invents reels for the
shoot, the template reel and the room's finalists are rendered, a separate network of viewer and judge agents
evaluates the rendered reels, and a simulated launch shows which one travels further. Everything a lab makes lives
in `data/shoots/<shoot>/creative/<lab id>/`: `state.json` (a `LabState`, `creative/schema.py`) and `reels/`.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/shoots/{id}/creative` | optional, any subset of `LabOptions`: `{finalists: 0-6, rounds: 1-3, creators: [id] \| null, single_agent, render_single_agent, voice, seed, followers, hours}` | `{lab_id}` (`20261028-210501-ab12cd`); the lab runs in the background |
| GET | `/api/shoots/{id}/creative` | | `[{id, title, created, status, phase, reels, usd, elapsed}]`, newest first; `reels` is the rendered reel count |
| GET | `/api/shoots/{id}/creative/{lab}` | | `LabState`: live while it runs (poll it; the file is rewritten at most twice a second) |
| POST | `/api/shoots/{id}/creative/{lab}/cancel` | | `{ok}`; `ok: false` if it was not running. It stops at its next step with `status: "cancelled"` |
| POST | `/api/shoots/{id}/creative/{lab}/resume` | | `{lab_id}` (the same id). Continues a failed or cancelled lab with its stored options, skipping finished work |
| POST | `/api/shoots/{id}/creative/{lab}/simulate` | `{followers?: 0-10000000, hours?: 1-720, seed?}`; a field left out keeps the lab's value | `[Simulation]`, one per reel, recomputed from the stored evaluation and saved (with the new values in `options`) |
| GET | `/api/shoots/{id}/creative/{lab}/files/{path}` | | a file under the lab folder, e.g. `reels/B.mp4`, `reels/B.jpg`. Honors `Range` (206 / 416), so `<video>` can seek; `Cache-Control: no-cache` with an ETag, because a resumed lab may re-render a reel under the same name |

`Reel.video` and `Reel.poster` are paths relative to the lab folder, so a reel plays from
`/api/shoots/{id}/creative/{lab}/files/{reel.video}` (add `?passcode=` when `DEMO_PASSCODE` is set). File paths
must stay inside the lab folder: `..`, absolute paths, backslashes, hidden names and symlinks out of it are
refused (400 `bad_path` or 404 `file_not_found`).

**Order and phases.** `phases` lists every phase in this order, each `{key, label, status, started, ended,
detail}` with times in seconds since the lab started; `phase` is the key of the phase in progress:

| key | label | |
|---|---|---|
| `brief` | Brief | the showrunner reads the photos and the listing |
| `pitch`, `critique`, `remix`, `greenlight` | Pitches, Critique, Remix, Greenlight | the writers' room; with `rounds: 1` critique and remix end `skipped` |
| `single_agent` | Single-agent control | one agent alone writes concepts from the same brief, in parallel with the room; `skipped` when `single_agent` is false |
| `render` | Render | `A` Template (`kind: baseline`), `B` Showrunner's pick, `C` Wildcard, `D`.. further finalists (`creative`), `S` the single agent's pick (`single_agent`, only with `render_single_agent`); two at a time by default (`RENDER_WORKERS`) |
| `watch`, `judge`, `novelty`, `verdict` | Audience, Judges, Novelty, Verdict | the evaluation network watches the rendered reels |
| `simulate` | Launch sim | `simulations`: one staged-distribution launch per reel (`creative/simulate.py`) |

`status` is `pending`, `running`, `done`, `failed` (with the error in `detail`; also `Cancelled.` for the phase a
cancel interrupted) or `skipped`. `feed` is the room's messages (`RoomMessage`) in order, including the producer's
own (`agent: "producer"`, `kind: "system"`); `agents` has a card for every agent id that appears in it.
`concepts` is the room's concepts, then the single agent's (`single_agent` lists their ids, its own pick first),
then the template. They, `critiques` and `finalists` fill in while the room is still working (each pitch, critique
and remix as it lands), and the room's final result replaces them when it returns; a cancelled lab keeps what had
landed. `usage` is the running model spend.

**Failure and resume.** A failed phase ends the lab with `status: "failed"` and `error: "<label> failed: ..."`,
keeping everything produced so far (the brief, the concepts, every reel that rendered). Resume continues without
redoing the brief, the room, the single agent, any reel whose files exist, or a finished evaluation. A `running`
lab whose `state.json` has not been rewritten for a minute (its process died) is reported as `failed`, and can be
resumed. One lab runs per shoot at a time; a second start, or resuming a lab that is running or done, is 409
`lab_conflict`.

**Simulated launch.** Wave 0 reaches a quarter of `followers`; each viewer persona is an audience segment
weighted by its share; its stop rate, watch time and 0-100 intents from the panel's reactions become per-view
rates (like 2-10%, comment 0.1-1%, share 0.2-2%, save 0.2-2%, follow 0.05-0.5%, inquiry 0.01-0.3%) and binomial
draws; the next wave's size follows the engagement score, plus 2-6 viewers per share. `series` holds hourly
cumulative counts for hours 0..`hours` (`totals` is the last point), `retention` the share of impressions still
watching per half second, `comments` the panel's own comment lines timed by when that segment's comments land,
`by_segment` views per persona id, and `notes` every assumption in plain words. Only the reactions go in, never
the reel's kind or its creativity scores, and the same `seed` reproduces the same launch.

Errors: 404 `shoot_not_found` / `lab_not_found`, 400 `bad_options` / `bad_simulation` / `bad_path`, 409
`lab_conflict`, 422 `invalid_request` (body types and ranges), 503 `agent_unavailable` (no Claude credentials).

CLI: `shootagent creative <shoot> [--finalists N] [--rounds N] [--no-voice] [--no-single-agent]
[--render-single-agent] [--resume LAB]` prints the feed and phase changes live and ends with a table of reels,
creativity, novelty, simulated views and cost; Ctrl-C cancels. `shootagent creative-sim <shoot> <lab> [--followers N]
[--hours N] [--seed N]` re-runs and saves the launch.
