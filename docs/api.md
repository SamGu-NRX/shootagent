# HTTP API

FastAPI app in `src/shootagent/api/`, served by `shootagent serve` on port 8000. The web UI's Vite dev
server proxies `/api` to it. JSON everywhere except blob and reel downloads. Errors are
`{"error": {"code": "...", "message": "plain sentence saying what to do"}}` with a 4xx/5xx status.
If `DEMO_PASSCODE` is set, every `/api` request needs the passcode: header `x-demo-passcode`, or query
`?passcode=` / cookie `demo_passcode` for requests that cannot set headers (EventSource, `<img>`).
Wrong or missing: 401 `passcode_required`. The static UI at `/` is not guarded, and neither is `/api/public/`
(the buyer page of a published listing, below).

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
| POST | `/api/shoots/{id}/film` | `{languages?: ["en", "es"], views?: [id]}`; default `["en"]`, script language first | `{run_id}` film run: the agent plans shots, makes or falls back each clip, writes and translates the script, narrates, and assembles one film per language. Trace over the usual `/runs/{run}/events` SSE stream. Films use approved views only (a view QC split into camera positions is replaced by its parts); with none approved, or `views` naming an unapproved one, 409 `needs_review` says which views need QC or approval. `shootagent launch <shoot>` runs QC on unchecked views, approves the ones QC left with no open or failed finding and did not advise against delivering (QC's `recommend_delivery` tool sets `view.meta.delivery = {deliver: false, reason, run}`), and makes the film. |
| GET | `/api/shoots/{id}/film` | | `{spec, manifest, variants: {<lang>: {url, cover, duration_s, captions, finisher, verify}}}`; `spec` and `manifest` are null before the first film |
| GET | `/api/shoots/{id}/film/manifest.json` | | the manifest (below); 404 `no_film` before the first assembly |
| GET | `/api/shoots/{id}/film/{lang}.mp4` | | the film in that language, 1080x1920, 30 fps |
| GET | `/api/shoots/{id}/film/{lang}/cover.png` | | its cover |

`spec` is `film/spec.py` FilmSpec: `{shots: [{view, end_view, motion: "generated"|"depth"|"still", camera, aspect,
move, label, seconds, result: {source, clips, attempts: [{fingerprint, backend, prompt, cached, passed, reasons,
judge, sheet}], fell_back, note}}], languages, narrations: {<lang>: {segments, labels, audio, backend}}, music,
disclosure, listing}`. `result.source` is what plays; it is `depth` after a fallback.

The manifest lists, per shot, `source_photos: [{view, version, blob, room, case}]` (the versions the clips were
made from), `labels` and `end_labels` (`{<lang>: str}`; a two-photo shot that fell back shows each photo under its
own label), `planned_motion`,
`motion_source`, `fell_back`, `ai_label` (true only for a generated clip that passed its check; it carries the
on-screen "AI-generated motion" label, California AB 723), `check: {passed, reasons, judge}` for the last
generated attempt, every `attempts` entry, and `times: {<lang>: [start_s, end_s]}` in film time. Clips rejected
under an earlier plan of the same run stay in the `attempts` of the shot that now shows that photo, with
`earlier_plan: true` and `planned: {view, end_view, label}`; ones whose photos the final plan dropped are in the
top-level `rejected_elsewhere`. Per language:
`{film, cover, duration_s, narration: {text, audio, backend, audio_s, timing: "transcript"|"word count"},
finisher: "reelengine"|"ffmpeg", captions, caption_cues, verify: {ok, fails, warns, lufs}, shot_times, cuts:
{narration_s, on_beat_s, beats_found, head_s}, cards: {<shot index>: file}, music: {track, title}, notes,
timings_s}`. The music bed is a track from `film/music/tracks.json` (default `cinematic-calm`, "The Morning
Foyer"), levelled down and limited before the finisher ducks it; `first-light-on-oak` is used only for a film
that ends inside its first 27 s, which are free of vocals, and otherwise swapped for the default with a note.
Beats are detected on the chosen bed. The last shot holds at most 4.5 s: the rest of a long final line plays
over a closing card (the last frame blurred, with the end card and the listing facts). Cuts sit at each
narration segment's start moved to the nearest beat of the music within 0.36 s (`on_beat_s`, source time). The
first shot carries a price card and the shot nearest the middle a square-footage card, rendered from Reel
Studio's `price-tag` and `stat-count-up` templates with the listing fields (left out when a field is missing). `notes` says why a language has no captions when it has none.

Film trace events, in addition to the common ones (`view` is the shot's photo):

- `media` `{shot?, language?, fingerprint, backend: "grok"|"replay"|"depth"|"still"|"gemini-tts", cached, seconds}`:
  a clip or narration produced, or served from the fingerprint cache.
- `check` `{shot, attempt, clip, passed, failures: [str], judge, signals: {endpoint_start, endpoint_end, motion,
  appearance, lines, coverage}, images: [contact sheet blob], ms}`: one generated clip checked against its
  photo(s). A film `check` has `clip` and no `version`. `judge` (v2, inventory then census): `{pass, failing:
  [{frame, kind: "new"|"missing"|"changed", category, item_id, description, where, in_photo_view, confidence}],
  not_counted: [same], changes: [{object, change, first_frame, where}] (failing in the v1 shape), confidence,
  inventory_items, frames_checked, form, strict, model, usages: [{purpose, model, input_tokens, output_tokens,
  usd}], usd}` or `{pass: false, error}`. Low-confidence findings never fail a clip and "missing" ones fail only
  at high confidence. For a room-to-room clip whose last frame shares under half its area with the start photo,
  `motion`, `appearance` and `lines` are `{checkable: false, pass: null, shared_with_start, why}`, and the clip
  fails with the reason "room-to-room walk: can't be verified against a photo" whatever the judge says. Scores on
  real clips: [docs/eval/film_checker.md](eval/film_checker.md).
- `retry` `{shot, attempt, max_attempts: 2, reason, next: "regenerate with the failure named in the prompt" |
  "depth fallback"}`: after a failed check, or when the backend cannot serve the request.
- `shot` `{shot, end_view, planned, source, fell_back, clips, note}`: what the shot will play.
- Depth shots first try a 40 px move (`settings.depth_strong_px`) and check it like a generated clip: its
  `check` event has `source: "depth"`; a failure emits `retry` with `source: "depth"` and `next: "24 px depth
  move"`. Those checks are in the shot's `result.depth_checks` and the manifest's `depth_checks`, never in
  `attempts`, so the disclosure page does not list them as AI clips. Generated-clip `check` events have
  `source: "generated"`.
- A film run started by `shootagent film-finish` (no agent: prepare_shots, narrate, assemble_film from the
  spec) has `run_start.request` "film-finish: ..." and no plan or model events. A `film_script` run (agent
  writes the script for a fixed plan; mode `film_script`) has only plan, tool and message events.
- `usage` with `purpose: "judge_clip"` for each clip check call and `"judge_clip_inventory"` for each photo
  inventory (made once per photo, then cached in `<data>/media/inventory/`).

## Listing

`src/shootagent/listing/`. A published listing has a public slug `<shoot id>-<token>`; the buyer page is
`/#/l/<slug>`, its disclosure `/#/l/<slug>/disclosure`. Unpublishing makes the slug answer 404 and keeps the
token, so a printed QR code works again after republishing. Records live in `<shoot>/listing/` (state, leads,
buyer sessions, answer cache). The film is read from the files the film pass writes (`<shoot>/film/`).

Behind the passcode:

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/shoots/{id}/listing/state` | | `ListingState`: `{published, token, published_at, public_origin, film_approved, note, reviewed_at, slug, public_base_url, notifications, leads: {total, new}}`; `notifications` is whether `NTFY_TOPIC` is set |
| POST | `/api/shoots/{id}/listing/publish` | `{published: bool, origin?: "https://host"}` | `ListingState`. Publishing needs `listing.address` (400 `needs_address`) and at least one photo. `origin` is where the realtor is browsing; it builds notification links when `PUBLIC_BASE_URL` is unset |
| POST | `/api/shoots/{id}/listing/review` | `{film_approved?: bool, note?: str}` (note up to 1000 chars) | `ListingState` |
| GET | `/api/shoots/{id}/listing/disclosure` | | `Disclosure` (below), to preview before publishing |
| GET | `/api/leads` | | `{listings: [{shoot_id, title, address, slug, published, film_approved, note, film_languages, thumb, leads_new}], leads: [Lead + {shoot_id, address}], notifications}`, newest lead first, across shoots that were ever published or have leads |
| POST | `/api/shoots/{id}/leads/{lead}/seen` | | `Lead` with `status: "seen"` |

Public, no passcode. Every route is rate-limited per IP (429 `rate_limited` with `Retry-After`): 240 reads a
minute; `ask` 6 a minute and 40 an hour; `book` 3 per 10 minutes and 8 a day. Behind the Cloudflare tunnel
the IP is `CF-Connecting-IP`, trusted only when the request arrives from loopback. POST bodies over 4 KB get
413. Unknown or unpublished slugs get 404 `listing_not_found`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/public/listings/{slug}` | | `{slug, listing, rooms: [{view, name, photo}], film: {languages, variants: {<lang>: {duration_s, captions, cover}}, shots: [{index, view, end_view, labels: {<lang>}, source, ai_label, times: {<lang>: [start_s, end_s]}}], disclosure_label} \| null, disclosure: {altered_photos, ai_clips}}`; `film` is null until a film is rendered |
| GET | `/api/public/listings/{slug}/photos/{blob}` | `?w=` long edge, 64-2048 | image; only the listing's published photos and their originals |
| GET | `/api/public/listings/{slug}/film/{lang}.mp4` | | the film |
| GET | `/api/public/listings/{slug}/film/{lang}/cover.png` | | its cover |
| GET | `/api/public/listings/{slug}/disclosure` | | `Disclosure` plus `address` |
| POST | `/api/public/listings/{slug}/ask` | `{question, language: "en"\|"es", session?}`; question 3-300 chars after cleaning | `{answer, status: "answered"\|"follow_up", rooms, cached, limited, session}`. Keep `session` and send it with later questions and the booking |
| POST | `/api/public/listings/{slug}/book` | `{name (80), contact: email or phone (120), preferred_time (120), language, session?}` | 201 `{lead, notifying}`; the lead carries the questions asked in `session` |

Q&A: one Claude call (`LISTING_QA_MODEL`, default `claude-sonnet-5-5`) per new question, with no tools and a
JSON schema for the answer. The model sees the listing fields, the room names and each photo's current
version; the question goes after the cached photo prefix inside `<question>` tags, as data. When the photos
and fields do not settle a question the model says what they show, status is `follow_up`, and the server adds
the "I've noted your question for the listing agent" line (the lead keeps the model's words only). Answers are
cached per question (case and punctuation ignored), language, and listing content. `LISTING_QA_DAILY_USD`
(default $1) caps spend per UTC day; past it, every question gets the follow-up answer with `limited: true`.
A model or credential failure also gets the follow-up answer and is not cached.

Booking notifies the realtor when `NTFY_TOPIC` is set: one POST to `https://ntfy.sh/<NTFY_TOPIC>` with the
buyer's name in the title, the address and requested time in the body (not the buyer's contact), and a click
URL `<PUBLIC_BASE_URL or publish origin>/#/r/<shoot>/<lead>`. The result is stored on the lead as
`notified: {sent, error, at}`; errors never contain the topic.

`Lead`: `{id, created_at, name, contact, preferred_time, language, questions: [{question, answer, status,
language, at}], status: "new"|"seen", notified}`.

`Disclosure` (California AB 723): `{law, photos: [{view, room, published, original, original_kind, altered,
changes}], film: {ai_clips: [{shot, label, view, end_view}], rejected_clips: [{shot, label, reasons, changes}],
label: {<lang>: str}, complete} | null}`. `published` and `original` are blob names; `original_kind` is
`fusion` (ShootAgent's fusion of the raw exposures), `exposure` (the camera's middle exposure) or `delivered`.
`changes` are records the page words per language: `{kind: "hdr", exposures}`, `{kind: "edit", op, note}` for
each version on the head's chain, `{kind: "fix", finding, explanation}` for each fixed QC finding. Photos come
in film order. `ai_clips` are shots whose generated clip passed its check and carries the on-screen label.

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
