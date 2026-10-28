# ShootAgent web UI

Vite + React + TypeScript. Talks to the FastAPI server through the dev proxy (`/api` → `:8000`).

```sh
npm install
npm run dev            # http://localhost:5173
npm run build          # type-check + production build into dist/ (the API server serves dist/ when it exists)
npm test               # trace reducer tests
```

## Data source

The header switch picks **Live server** or **Demo data**; `?api=mock` or `?api=live` in the URL does the same and is remembered.

- Live: start the API with `.venv/Scripts/python -m shootagent.cli serve` from the repo root. To proxy to another port: `SHOOTAGENT_API=http://127.0.0.1:8010 npm run dev`.
- Demo data runs entirely in the browser (`src/api/mock/`). Runs are scripted trace replays, so they never call a model. `?speed=2` plays them faster. The QC tour script shows plan → tool calls → failed check → retry → passed check → finding fixed (living room), plus a defect that fails twice and is flagged for review (garage).

Mock images come from the AutoHDR defect set: `.venv/Scripts/python web/scripts/make_mock_assets.py` rewrites `public/mock/`.

## Screenshots

`screenshots/live-*.jpg` come from real agent runs on the showcase demo shoot at 1920x1080:

| File | Shows |
|---|---|
| `live-00-start.jpg` | Start screen with the showcase preset |
| `live-01-qc-midrun-check.jpg` | QC streaming: candidate chips, a failed self-check, the retry |
| `live-02-qc-done.jpg` | QC finished: totals, split views, the agent's summary |
| `live-03-fixed-finding-slider.jpg` | Restored stop sign, zoomed, slider through it, the passing check |
| `live-04-chat-results.jpg` | "remove the cars and trash bins": applied and not-needed lines |
| `live-05-click-to-mask.jpg` | Shift-clicked TV mask attached to the next request |
| `live-06-tv-result-retry.jpg` | The TV result, with the failed first attempt and retry in the trace |
| `live-07-reel-playing.jpg` | The 9:16 reel in the Reel tab |

`node scripts/live-screenshots.mjs <shootId>` regenerates live-03 to live-07 from a shoot that already has
those runs. It reads runs only, needs Microsoft Edge (Playwright's Chromium can't play H.264), and both
servers running. `npm run screenshots` captures the demo-data set (`01-` to `13-`).

## Keys

← / → photos · `\` before/after · Ctrl+Z / Ctrl+Shift+Z undo/redo (moves the view's head) · `+` `-` `0` `1` zoom · `F` findings · `M` mask · Shift-click marks an object (Shift+Alt-click excludes) · Enter sends chat.
