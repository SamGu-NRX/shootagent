# HTTP API

FastAPI app in `src/shootagent/api/`, served by `shootagent serve` on port 8000. The web UI's Vite dev
server proxies `/api` to it. JSON everywhere except blob and reel downloads. Errors are
`{"error": {"code": "...", "message": "plain sentence saying what to do"}}` with a 4xx/5xx status.
If `DEMO_PASSCODE` is set, every request needs header `x-demo-passcode`.

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
| POST | `/api/shoots/{id}/views/{view}/masks` | `{points: [[x,y,label]], box?: [x0,y0,x1,y1]}` normalised | `Mask` (SAM 2.1 from user clicks) |

## Agent runs

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/shoots/{id}/qc` | `{views?: [id]}` | `{run_id}` autonomous QC pass |
| POST | `/api/shoots/{id}/chat` | `{message, views?: [id], mask?: id}` | `{run_id}` chat request |
| POST | `/api/shoots/{id}/reel` | `{style?: {...}}` | `{run_id}` reel assembly |
| GET | `/api/shoots/{id}/runs` | | `[{id, mode, started, done, usd, summary}]` |
| GET | `/api/shoots/{id}/runs/{run}/events` | | SSE stream: one `event: trace` per trace event (trace.py shape), replaying past events first; closes after `run_end` |
| POST | `/api/shoots/{id}/runs/{run}/cancel` | | `{ok}` |
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

`check` events: `{version, passed, metrics: {outside_max_delta, region_ssim_to_reference, ...},
judge: {verdict: "pass"|"fail", reason, model}}`. `retry` events: `{attempt, reason, next}`.
`usage` events carry token counts and `usd`; `run_end` carries totals.
