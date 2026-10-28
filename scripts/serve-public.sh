#!/usr/bin/env bash
# Serve ShootAgent from this machine at a public HTTPS URL through a Cloudflare tunnel.
# The tunnel exposes only the app on 127.0.0.1:8000; model keys stay in .env on this machine.
#
#   DEMO_PASSCODE=... scripts/serve-public.sh            # quick tunnel: random *.trycloudflare.com URL
#   DEMO_PASSCODE=... TUNNEL=shootagent scripts/serve-public.sh   # named tunnel with a stable hostname
#                                                        # (needs `cloudflared tunnel login` + `tunnel create`)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${DEMO_PASSCODE:-}" ]; then
  echo "Set DEMO_PASSCODE: the public URL spends this machine's model credits, so it needs a passcode." >&2
  exit 1
fi
command -v cloudflared >/dev/null || { echo "Install cloudflared first (scoop install cloudflared / brew install cloudflared)." >&2; exit 1; }
[ -f web/dist/index.html ] || (cd web && npm ci && npm run build)

export DEMO_PASSCODE
# Public traffic bills a real Anthropic key (git-ignored .env.public: ANTHROPIC_API_KEY=...) rather than the
# local gateway. Blanking the gateway variables makes the app send that key to Anthropic's API; image edits
# keep using OPENAI_* from .env.
if [ -f .env.public ]; then
  set -a; . ./.env.public; set +a
  export ANTHROPIC_BASE_URL="" ANTHROPIC_AUTH_TOKEN=""
fi
uv run shootagent serve --host 127.0.0.1 --port 8000 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
until curl -fsS -o /dev/null http://127.0.0.1:8000/; do sleep 1; done

if [ -n "${TUNNEL:-}" ]; then
  cloudflared tunnel run --url http://127.0.0.1:8000 "$TUNNEL"
else
  cloudflared tunnel --url http://127.0.0.1:8000
fi
