#!/usr/bin/env bash
# Start the local Claude→Vertex OpenAI-compatible proxy using the AEH .eval-venv.
# Run this in a normal terminal (not inside Cursor's agent sandbox).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.eval-venv/bin/python"
PROXY_SRC="${CLAUDE_PROXY_SRC:-/tmp/claude_proxy.py}"
PORT="${CLAUDE_PROXY_PORT:-11434}"
LOG="${CLAUDE_PROXY_LOG:-/tmp/claude_proxy.log}"

if [[ ! -x "$PY" ]]; then
  echo "error: missing $PY — create/activate AEH .eval-venv first" >&2
  exit 1
fi
if [[ ! -f "$PROXY_SRC" ]]; then
  echo "error: missing $PROXY_SRC" >&2
  exit 1
fi

for p in $(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true); do
  echo "killing pid $p on :$PORT"
  kill -9 "$p" || true
done
# Also clear the stale 18080 instance if present
for p in $(lsof -tiTCP:18080 -sTCP:LISTEN 2>/dev/null || true); do
  echo "killing pid $p on :18080"
  kill -9 "$p" || true
done
sleep 1

export NO_PROXY='*' no_proxy='*'
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy 2>/dev/null || true
export ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-itpc-gcp-eco-eng-claude}"
export CLOUD_ML_REGION="${CLOUD_ML_REGION:-us-east5}"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"

# Rewrite listen port if needed
TMP_PROXY="$(mktemp)"
sed "s/port=11434/port=${PORT}/" "$PROXY_SRC" >"$TMP_PROXY"

nohup "$PY" "$TMP_PROXY" >"$LOG" 2>&1 &
echo "proxy_pid=$! log=$LOG"
sleep 2
curl -sf "http://127.0.0.1:${PORT}/health"
echo
curl -sf "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4","messages":[{"role":"user","content":"Reply with exactly: pong"}],"max_tokens":16}'
echo

openshell provider update inference \
  --config "OPENAI_BASE_URL=http://host.containers.internal:${PORT}/v1" \
  --credential OPENAI_API_KEY=empty

echo "Ready. Point AEH at Quay with:"
echo "  export OPENSHELL_GATEWAY_ENDPOINT='http://[::1]:17670'"
echo "  export AGENT_EVAL_OPENSHELL_IMAGE='quay.io/aipcc/base-images/agentic/openclaw:latest'"
echo "  export AGENT_EVAL_OPENSHELL_POLICY='${ROOT}/deploy/openshell/eval-policy.yaml'"
echo "  export AGENT_EVAL_OPENSHELL_PROVIDER=inference"
