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
| POST | `/api/shoots/{id}/copy` | | `{run_id}` copy run: the agent writes the headline, the MLS remarks, and a caption and alt text per listing photo; copy that fails the fair-housing check is refused by the tools |
| GET | `/api/shoots/{id}/runs` | | `[{id, mode, request, started, done, status, usd, summary}]`; `status` is `queued`, `running`, `done`, `cancelled`, or `unfinished` (no `run_end` and not live) |
| GET | `/api/shoots/{id}/runs/{run}/events` | | SSE stream: one `event: trace` per trace event (trace.py shape, SSE `id` = `seq`), replaying past events first; closes after `run_end`. Honors `Last-Event-ID`. A run with no `run_end` that is not live (server restarted) ends with a synthetic `run_end` `{ok: false}` so EventSource stops reconnecting. A queued run's stream stays open and starts at its `run_start`. |
| POST | `/api/shoots/{id}/runs/{run}/cancel` | | `{ok}`; `ok: false` if the run already ended. A queued run ends at once with `run_end {ok: false}`; a running one stops at its next step. |
| GET | `/api/shoots/{id}/reel/latest.mp4` | | video |

A chat message while a run is active on the same shoot is queued, not run in parallel. A chat run's `run_start`
carries `origin` (`photographer` or `client`), `comment` (the client comment it answers) and `policy` (the
listing photo policy's verdict on the request: `{verdict, items: [{text, verdict, category, reason}]}`, verdicts
`decline`, `disclose`, `overlay`, `exempt`, `unclear`). In chat mode the loop refuses an edit call the policy
declines (`tool_result {ok: false, data: {refused: true}}`), and `report_view_result` may report a part as
`declined`.

## Delivery: disclosures, publication, client review, listing kit

California's AB 723 requires a digitally altered listing photo to say so and to be paired with the unaltered
original. `compliance/` derives both from the op log and the QC verdicts.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/shoots/{id}/compliance` | | `{photos: [{view, label, status, changes, disclosure, ...}], counts, ready, issues, listing, stale, publication, public_token}`. `status` is `altered`, `unaltered`, `unverified` (QC has not compared it with its brackets), `review` (a QC finding that may be an alteration needs a ruling) or `superseded` (replaced by regrouping). `changes[].kind` is `alteration`, `adjustment`, `restoration`, `overlay` or `possible_alteration`. `listing` is the set that publishes: approved photos, all deliverable ones when none is approved. |
| POST | `/api/shoots/{id}/views/{view}/findings/{finding}` | `{verdict: "disclose" \| "not_alteration", note?}` | `View`. The photographer's ruling on a finding that may be an alteration; recorded on the finding. |
| POST | `/api/shoots/{id}/publish` | | publication summary `{token, version, created_at, root, photos}`. Writes listing-size JPEGs, pairs each altered photo with its original (the delivered photo, or the bracket fusion registered onto the photo's frame), and a SHA-256 hash chain per photo. The token survives republishing. |
| POST | `/api/shoots/{id}/shares` | `{label?}` | `Share {token, label, created_at, revoked, max_requests}`: a client review link |
| DELETE | `/api/shoots/{id}/shares/{token}` | | `{ok}`; the link stops working |
| GET | `/api/shoots/{id}/comments` | | client requests with their live state (below) |
| POST | `/api/shoots/{id}/kit` | `{include_reel?: bool}` | kit info `{name, size, files: [{path, size}], built_at, publication, photos, altered, url}`; publishes first when the photos changed |
| GET | `/api/shoots/{id}/kit` | | the latest kit info, or `null` |
| GET | `/api/shoots/{id}/kit/latest.zip` | | the zip |
| POST | `/api/lint/listing` | `{text}` | fair-housing wording check `{ok, flags: [{phrase, start, end, severity: "avoid" \| "review", suggestion, why}], note}` |
| POST | `/api/lint/request` | `{text}` | the listing photo policy's verdict on a request |

### Token routes (no passcode; the token is the credential)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/public/{token}` | | the public disclosure page: `{title, listing, version, published_at, root, photos: [{view, label, order, status, disclosure, changes, final, original, original_kind, capture, chain: [{hash, body}]}]}`. Each chain entry's `hash` is sha256 of `body` (canonical JSON); `body.prev` is the previous entry's hash; images carry the sha256 of the file's bytes. |
| GET | `/api/public/{token}/blobs/{blob}` | `?w=` | only files in the publication |
| GET | `/api/review/{token}` | | `{title, listing, requests_left, agent_available, public_token, views: [{id, label, blob, original_blob, edited, client, status, disclosure}], comments}` |
| GET | `/api/review/{token}/blobs/{blob}` | `?w=` | image bytes |
| POST | `/api/review/{token}/views/{view}/verdict` | `{status: "approved" \| "changes_requested" \| null}` | the review payload |
| POST | `/api/review/{token}/comments` | `{view, text, point?: [x, y]}` | the comment with its state. A request the policy declines entirely is answered at once (`state: "declined"`, no run). Otherwise a `point` becomes a SAM mask and a chat run starts with `origin: "client"`, scoped to that photo; with no model configured or the link's budget spent, the comment is kept for the photographer (`state: "sent"`). |
| POST | `/api/review/{token}/comments/{comment}/undo` | | the comment; the photo's head goes back to where it was before the request |
| GET | `/api/review/{token}/runs/{run}/events` | | SSE, as for a shoot's runs, for runs this link started |

Comment `state`: `declined`, `sent`, `queued`, `working`, `applied`, `not_needed`, `failed`, `undone`, `stopped`.

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
