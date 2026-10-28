# Contributing

## Pull requests

Keep a pull request focused. A larger change is fine when its parts belong together.

State what the old code did before describing the new behavior. Link the reviewed files where that helps. Show only changed UI behavior, list known gaps, and report commands that actually ran with their results.

The `size:*` label describes the effective diff. It is a review signal, not a merge gate. Do not set it by hand.

## Checks

| Check | Command |
|---|---|
| Python lint and tests | `uv sync --extra dev && uv run ruff check src tests eval && uv run pytest -q` |
| Web typecheck and build | `cd web && npm ci && npm run build` |

Tests that need local GPU models skip when torch is not installed, so CI covers the deterministic imaging code, the agent loop with a scripted model, and the API. Run `uv sync --extra local --extra dev` locally to include the model tests.

Keep workflows that run pull-request code away from production credentials and destructive external systems. A green CI run is evidence for the checks it ran, not proof of product or deployment acceptance.
