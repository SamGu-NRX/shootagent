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

## Listing film

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/shoots/{id}/film` | `{languages?: ["en", "es"], views?: [id]}`; default `["en"]`, script language first | `{run_id}` film run: the agent plans shots, makes or falls back each clip, writes and translates the script, narrates, and assembles one film per language. Trace over the usual `/runs/{run}/events` SSE stream. |
| GET | `/api/shoots/{id}/film` | | `{spec, manifest, variants: {<lang>: {url, cover, duration_s, captions, finisher, verify}}}`; `spec` and `manifest` are null before the first film |
| GET | `/api/shoots/{id}/film/manifest.json` | | the manifest (below); 404 `no_film` before the first assembly |
| GET | `/api/shoots/{id}/film/{lang}.mp4` | | the film in that language, 1080x1920, 30 fps |
| GET | `/api/shoots/{id}/film/{lang}/cover.png` | | its cover |

`spec` is `film/spec.py` FilmSpec: `{shots: [{view, end_view, motion: "generated"|"depth"|"still", camera, aspect,
move, label, seconds, result: {source, clips, attempts: [{fingerprint, backend, prompt, cached, passed, reasons,
judge, sheet}], fell_back, note}}], languages, narrations: {<lang>: {segments, labels, audio, backend}}, music,
disclosure, listing}`. `result.source` is what plays; it is `depth` after a fallback.

The manifest lists, per shot, `source_photos: [{view, version, blob, room, case}]`, `planned_motion`,
`motion_source`, `fell_back`, `ai_label` (true only for a generated clip that passed its check; it carries the
on-screen "AI-generated motion" label, California AB 723), `check: {passed, reasons, judge}` for the last
generated attempt, every `attempts` entry, and `times: {<lang>: [start_s, end_s]}` in film time. Per language:
`{film, cover, duration_s, narration: {text, audio, backend, audio_s, timing: "transcript"|"word count"},
finisher: "reelengine"|"ffmpeg", captions, caption_cues, verify: {ok, fails, warns, lufs}, shot_times, notes,
timings_s}`. `notes` says why a language has no captions when it has none.

Film trace events, in addition to the common ones (`view` is the shot's photo):

- `media` `{shot?, language?, fingerprint, backend: "grok"|"replay"|"depth"|"still"|"gemini-tts", cached, seconds}`:
  a clip or narration produced, or served from the fingerprint cache.
- `check` `{shot, attempt, clip, passed, failures: [str], judge: {pass, changed, changes: [{object, change,
  first_frame, where}], confidence, model, usd} | null, signals: {endpoint_start, endpoint_end, motion,
  appearance, lines, coverage}, images: [contact sheet blob], ms}`: one generated clip checked against its
  photo(s). A film `check` has `clip` and no `version`.
- `retry` `{shot, attempt, max_attempts: 2, reason, next: "regenerate with the failure named in the prompt" |
  "depth fallback"}`: after a failed check, or when the backend cannot serve the request.
- `shot` `{shot, end_view, planned, source, fell_back, clips, note}`: what the shot will play.
- `usage` with `purpose: "judge_clip"` for each vision judgement of a clip.

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
