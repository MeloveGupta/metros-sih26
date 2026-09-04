#!/usr/bin/env bash
# Start the Metros API with AI label reading enabled.
#
# Reads the Claude Code OAuth access token from the macOS keychain (after you
# have run `/login` in Claude Code, or `claude login`) and passes it to the
# server as ANTHROPIC_AUTH_TOKEN. No API key needed. The token never appears in
# the repo or in shell history.
#
# Usage:  ./scripts/run_api.sh            (foreground)
#         ./scripts/run_api.sh --bg       (background, logs to /tmp/ms_api.log)
set -euo pipefail
cd "$(dirname "$0")/.."

TOKEN=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('claudeAiOauth',{}).get('accessToken',''))" 2>/dev/null || true)

if [ -z "${TOKEN:-}" ]; then
  echo "No Claude Code OAuth token found. Run /login in Claude Code first."
  echo "Starting WITHOUT AI (offline OCR only)."
else
  echo "AI label reading enabled (OAuth token, len ${#TOKEN})."
fi

export ANTHROPIC_AUTH_TOKEN="${TOKEN:-}"
export JWT_SECRET="${JWT_SECRET:-local-demo-secret-change-me-32bytes!!}"

CMD=(.venv/bin/uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --log-level warning)
if [ "${1:-}" = "--bg" ]; then
  nohup env ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN" JWT_SECRET="$JWT_SECRET" \
    "${CMD[@]}" >/tmp/ms_api.log 2>&1 &
  echo "API starting in background (pid $!). Logs: /tmp/ms_api.log"
else
  exec "${CMD[@]}"
fi
