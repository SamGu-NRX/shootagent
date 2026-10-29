# ShootAgent site

The public landing page for ShootAgent. It links to the live demo at <https://shootagent.sillion.app> and uses
the product's look: the same tokens as `web/src/styles/tokens.css`, the same Instrument Sans and IBM Plex Mono.

```bash
cd site
npm install
npm run dev        # http://127.0.0.1:5174
npm run build      # type-check, build, then prerender the page's HTML into dist/index.html
npm run preview    # serve dist/ on http://127.0.0.1:4174
```

## What is on it

Every photo is real output from ShootAgent's own tools on AutoHDR's hackathon data, made without any model API:

- **Hero.** `run_qc_checks` finds a stop sign the editor's camera-removal step erased from `defect_train_0016`,
  and `restore_from_brackets` puts it back through its mask (nothing outside the mask changes). The customer
  edits (wall colour, the car) are the README's before/after composites.
- **The split.** `run_qc_checks` finds that `defect_train_0044` was fused from two camera positions, and
  `regroup_brackets` fuses each group into its own photo.
- **The record.** The fingerprints are SHA-256 of this page's own image files, computed in the browser, with the
  same canonical-JSON chain as `src/shootagent/compliance/provenance.py`.
- **The policy box.** An abridged copy of `src/shootagent/compliance/policy.py` (`src/lib/policy.ts`).

To rebuild the images and `src/assets.json` from the tools (needs the `local` extra and `AUTOHDR_DEFECT_DIR`):

```bash
uv run python site/scripts/prepare_images.py
```

`public/og.jpg`, the link preview, is a 1200 x 630 capture of the hero at its first step.

## Motion

After the BaseScanning landing page: the markup is the finished state, and each beat is a data attribute that a
CSS transition carries. A figure plays once, the first time it scrolls into view, and only if it started below
the screen. Reduced motion shows every finished state at once, and the hero's walkthrough pauses (it can still
be played, and it stops while off screen or in a background tab). Keyboard use makes controls change without
animating.

## Deploy

A Vercel project with `site` as its root directory (`vercel.json` sets the Vite build and long caching for
`/img/*`). From this folder: `vercel deploy --prod`.
