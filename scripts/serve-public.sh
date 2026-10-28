#!/usr/bin/env bash
# Serve ShootAgent from this machine at a public HTTPS URL through a Cloudflare tunnel.
# The tunnel exposes only the app on 127.0.0.1:8000; model keys stay on this machine.
#
#   scripts/serve-public.sh                    # quick tunnel: random *.trycloudflare.com URL
#   TUNNEL=shootagent scripts/serve-public.sh  # named tunnel with a stable hostname
#                                              # (needs `cloudflared tunnel login` + `tunnel create`)
#
# .env.local (git-ignored) holds DEMO_PASSCODE and, optionally, ANTHROPIC_API_KEY. The file is read, not
# sourced, and only those two names are taken from it; they go to the app server alone, never to cloudflared.
set -euo pipefail
cd "$(dirname "$0")/.."

local_value() {  # last NAME=value line in .env.local, quotes and CR stripped; empty when absent
  [ -f .env.local ] || return 0
  grep -E "^$1=" .env.local | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

passcode="${DEMO_PASSCODE:-$(local_value DEMO_PASSCODE)}"
api_key="${ANTHROPIC_API_KEY:-$(local_value ANTHROPIC_API_KEY)}"
if [ -z "$passcode" ]; then
  echo "Set DEMO_PASSCODE in .env.local: the public URL spends this machine's model credits, so it needs a passcode." >&2
  exit 1
fi
command -v cloudflared >/dev/null || { echo "Install cloudflared first (scoop install cloudflared / brew install cloudflared)." >&2; exit 1; }
[ -f web/dist/index.html ] || (cd web && npm ci && npm run build)

# With a real Anthropic key, public traffic bills that key: blanking the gateway variables makes the app send
# it to Anthropic's API (image edits keep using OPENAI_* from .env). Without one, the app uses the gateway.
server_env=(DEMO_PASSCODE="$passcode")
if [ -n "$api_key" ]; then
  server_env+=(ANTHROPIC_API_KEY="$api_key" ANTHROPIC_BASE_URL="" ANTHROPIC_AUTH_TOKEN="")
fi
if command -v uv >/dev/null; then run=(uv run); else run=(python -m uv run); fi
env "${server_env[@]}" "${run[@]}" shootagent serve --host 127.0.0.1 --port 8000 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
until curl -fsS -o /dev/null http://127.0.0.1:8000/; do sleep 1; done

# The tunnel needs no credentials of ours; strip any it would otherwise inherit from this shell.
tunnel_env=(env -u DEMO_PASSCODE -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY)
if [ -n "${TUNNEL:-}" ]; then
  "${tunnel_env[@]}" cloudflared tunnel run --url http://127.0.0.1:8000 "$TUNNEL"
else
  "${tunnel_env[@]}" cloudflared tunnel --url http://127.0.0.1:8000
fi
