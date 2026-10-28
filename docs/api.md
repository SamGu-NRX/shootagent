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
