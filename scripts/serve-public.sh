#!/usr/bin/env bash
# Serve ShootAgent from this machine at a public HTTPS URL through a Cloudflare tunnel.
# The tunnel exposes only the app on 127.0.0.1:8000; model keys stay on this machine.
#
#   scripts/serve-public.sh                    # quick tunnel: random *.trycloudflare.com URL
#   TUNNEL=shootagent scripts/serve-public.sh  # named tunnel with a stable hostname
#                                              # (needs `cloudflared tunnel login` + `tunnel create`)
#
# .env.local (git-ignored) holds DEMO_PASSCODE and, optionally, ANTHROPIC_API_KEY, NTFY_TOPIC (tour requests
# pushed to the realtor's phone; anyone with the topic can read them) and PUBLIC_BASE_URL (the tunnel's public
# URL, for QR codes and notification links). The file is read, not sourced, and only those names are taken
# from it; they go to the app server alone, never to cloudflared.
set -euo pipefail
cd "$(dirname "$0")/.."

local_value() {  # last NAME=value line in .env.local, quotes and CR stripped; empty when absent
  [ -f .env.local ] || return 0
  grep -E "^$1=" .env.local | tail -n 1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

passcode="${DEMO_PASSCODE:-$(local_value DEMO_PASSCODE)}"
api_key="${ANTHROPIC_API_KEY:-$(local_value ANTHROPIC_API_KEY)}"
ntfy_topic="${NTFY_TOPIC:-$(local_value NTFY_TOPIC)}"
public_url="${PUBLIC_BASE_URL:-$(local_value PUBLIC_BASE_URL)}"
# Live TTS needs a Gemini key. launchd passes no environment, so on macOS read it from the login Keychain; it is
# never written to a file or printed.
gemini_key="${GEMINI_API_KEY:-}"
if [ -z "$gemini_key" ] && command -v security >/dev/null; then
  gemini_key="$(security find-generic-password -a "$USER" -s "${GEMINI_KEYCHAIN_ITEM:-gemini-api-key-spurs-film}" -w 2>/dev/null || true)"
fi
if [ -z "$passcode" ]; then
  echo "Set DEMO_PASSCODE in .env.local: the public URL spends this machine's model credits, so it needs a passcode." >&2
  exit 1
fi
command -v cloudflared >/dev/null || { echo "Install cloudflared first (scoop install cloudflared / brew install cloudflared)." >&2; exit 1; }

# Rebuild the web app when the checked-out commit differs from the one it was built from; checking only for a
# missing build kept serving the old UI after a branch switch. A failed rebuild keeps the previous build up.
rev="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [ ! -f web/dist/index.html ] || [ "$(cat web/dist/.build-rev 2>/dev/null)" != "$rev" ]; then
  if (cd web && npm ci && npm run build); then
    echo "$rev" > web/dist/.build-rev
  elif [ -f web/dist/index.html ]; then
    echo "$(date -u +%FT%TZ) serve-public: web build failed; serving the previous build." >&2
  else
    echo "Web build failed and there is no previous build to serve." >&2
    exit 1
  fi
fi

# With a real Anthropic key, public traffic bills that key: blanking the gateway variables makes the app send
# it to Anthropic's API (image edits keep using OPENAI_* from .env). Without one, the app uses the gateway.
server_env=(DEMO_PASSCODE="$passcode")
[ -n "$ntfy_topic" ] && server_env+=(NTFY_TOPIC="$ntfy_topic")
[ -n "$public_url" ] && server_env+=(PUBLIC_BASE_URL="$public_url")
[ -n "$gemini_key" ] && server_env+=(GEMINI_API_KEY="$gemini_key")
if [ -n "$api_key" ]; then
  server_env+=(ANTHROPIC_API_KEY="$api_key" ANTHROPIC_BASE_URL="" ANTHROPIC_AUTH_TOKEN="")
fi
if command -v uv >/dev/null; then run=(uv run); else run=(python -m uv run); fi
env "${server_env[@]}" "${run[@]}" shootagent serve --host 127.0.0.1 --port 8000 &
server=$!
tunnel=""
trap 'kill $server $tunnel 2>/dev/null' EXIT
trap 'exit 143' TERM INT
until curl -fsS -o /dev/null http://127.0.0.1:8000/; do
  kill -0 "$server" 2>/dev/null || { echo "App server exited during startup." >&2; exit 1; }
  sleep 1
done

# The tunnel needs no credentials of ours; strip any it would otherwise inherit from this shell.
tunnel_env=(env -u DEMO_PASSCODE -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u OPENAI_API_KEY -u NTFY_TOPIC -u GEMINI_API_KEY)
if [ -n "${TUNNEL:-}" ]; then
  "${tunnel_env[@]}" cloudflared tunnel run --url http://127.0.0.1:8000 "$TUNNEL" &
else
  "${tunnel_env[@]}" cloudflared tunnel --url http://127.0.0.1:8000 &
fi
tunnel=$!

# Exit when either process dies, so a supervisor (launchd KeepAlive) restarts both. Without this a dead app
# server leaves the tunnel up and the public URL answers 502 until someone notices. Polling, because macOS
# /bin/bash 3.2 has no `wait -n`.
while kill -0 "$server" 2>/dev/null && kill -0 "$tunnel" 2>/dev/null; do sleep 5; done
echo "$(date -u +%FT%TZ) serve-public: $(kill -0 "$server" 2>/dev/null && echo tunnel || echo 'app server') exited; stopping." >&2
exit 1
