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

`npm run screenshots` (with `npm run dev` running) drives the demo data with Playwright and writes `screenshots/`.

## Keys

← / → photos · `\` before/after · Ctrl+Z / Ctrl+Shift+Z undo/redo (moves the view's head) · `+` `-` `0` `1` zoom · `F` findings · `M` mask · Shift-click marks an object (Shift+Alt-click excludes) · Enter sends chat.
