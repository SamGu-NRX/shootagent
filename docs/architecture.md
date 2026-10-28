# ShootAgent architecture

ShootAgent takes a real-estate shoot (each view's exposure brackets plus AutoHDR's finished photo), finds
and fixes editing defects on its own, applies customer chat requests across the whole shoot, and cuts a
9:16 listing reel from the approved photos. Every step is visible as a trace.

## 1. The rule

**The agent chooses tools; deterministic code and pinned models make the pixels.** Claude never writes
code or touches an array. It reads photos and tool results and calls typed tools (section 5). Each edit
tool reads one image version and writes a new one, so every result can be reproduced from the op log
and undone by moving the view's head back. This is the pattern from reel-studio-app
(`docs/architecture.md` there): the AI edits a project file; an engine renders it.

**Pixels outside an edit's mask never change.** Generative and local edits are composited back through
the mask (`imaging/composite.py`), and every edit's self-check measures the outside-mask delta.

## 2. Layout

```
src/shootagent/
  config.py        settings from env / .env
  store.py         shoot.json + content-addressed blobs + versions + masks   (contract, see 3)
  trace.py         run event log, jsonl + live SSE fan-out                     (contract, see 4)
  imaging/         deterministic image ops: numpy + OpenCV only, no network, no torch
  models/          model adapters: local torch models and remote APIs, lazily loaded
  qc/              autonomous QC analysis (candidate defects from bracket evidence)
  tools/           the agent's typed tools (tools/base.py is the contract)
  agent/           Claude tool-use loop, prompts, self-check enforcement
  reel/            reel spec + ffmpeg renderer
  api/             FastAPI app (docs/api.md)
  track2/          adapter for AutoHDR's Track 2 case.json contract
  ingest.py        AutoHDR dataset cases and editor requests -> shoots
  cli.py           `shootagent ...`
web/               Vite + React + TypeScript UI
eval/              dataset runs and metrics
```

Ownership boundaries: `imaging/` never imports torch or makes network calls; `models/` never decides what
to edit; `tools/` is the only layer the agent sees; `api/` only wires HTTP to store, agent and tools.

## 3. Store (`store.py`)

`data/shoots/<id>/shoot.json` holds the shoot: views, each with its brackets, the AutoHDR finished photo
(`finished`), our bracket fusion (`reference`), a version list with `head`, findings in AutoHDR's
format (`type` from the taxonomy, normalised `region` box), and approval. Masks are separate records
(single-channel PNG blobs). Blobs are named by content hash and never rewritten. Intermediate images
are PNG so repeated edits do not accumulate JPEG loss. Use `ShootStore.add_version`, `add_mask`,
`mutate`; never write shoot.json directly.

## 4. Trace (`trace.py`)

One jsonl file per agent run. Event kinds: `run_start, plan, thought, tool_call, tool_result, check,
retry, finding, message, usage, run_end, error`. `TraceRun.emit` is thread-safe; the API streams events
over SSE. The UI renders the trace from events alone.

## 5. Tools

Perception: `list_views`, `view_photo` (returns a downscaled image to the model), `run_qc_checks`,
`find_regions` (text prompt -> masks via Grounding DINO + SAM 2.1), `mask_from_box`, `sample_color`
(hex from the finished photo or the exposure-normalised brackets), `compare_to_brackets`.

Edits (each returns a new version): `adjust_exposure`, `adjust_white_balance`, `recolor_region`,
`remove_object` (LaMa, or generative fill), `generative_fill`, `restore_from_brackets`,
`regroup_brackets`, `place_on_screen`, `add_watermark`, `draw_lot_lines`, `revert`.

Shoot: `set_finding`, `approve_view`, `set_listing`, reel tools.

**Self-check is enforced by the loop, not left to the model.** After every edit tool the loop runs
`verify_edit`: deterministic metrics (outside-mask delta, region metric such as delta-E to a target hex
or similarity to the bracket reference) plus a Claude vision judgement on before/after/reference crops
against the stated intent. A failed check moves the head back to the parent, emits `check` and `retry`
events, and returns the failure reason to the model, which must try a different tool or parameters
(capped by `max_fix_attempts`).

## 6. QC strategy

The finished photo should be "the brackets, merged and styled". Most defect families in AutoHDR's set
show up as a disagreement between the finished photo and an aligned fusion of the brackets:

| Family (count in dataset) | Evidence | Fix |
|---|---|---|
| ghosting_grouping (21) | pairwise feature matching splits brackets into groups; residual misalignment after alignment; finished photo matching two groups | regroup, align, fuse each group; restyle to the finished photo's tone |
| camera_person_removal (14; 9 are objects removed by mistake) | structure present in brackets but missing from the finished photo; vision check for leftover tripod/person/reflection | `restore_from_brackets` for false removals; `remove_object` for leftovers |
| fireplace (7) | new bright structure in finished photo absent from brackets | restore region from brackets, or generative fire where requested |
| paint_color / wood_floor_color (8) | chroma residual after a global colour fit between finished and reference | `recolor_region` to the bracket's exposure-normalised hue |
| window_sky (4), tv_art (1), geometry_lens (4) | diff regions adjudicated by vision | restore from brackets / flag |

Deterministic analysis proposes candidate regions with evidence; the agent adjudicates them with
vision (a legitimate sky replacement or declutter is not a defect) and picks the fix.
