#!/usr/bin/env bash
# Host prerequisite: Crabline Slack local provider server for OpenClaw evals.
# Run this before AEH → OpenShell → Quay OpenClaw channel cases.
#
# Usage:
#   ./examples/start-crabline-slack.sh
#   # ready file: .tmp/crabline/ready/slack-server.json
#   # sandbox must use host.openshell.internal:8787 (not 127.0.0.1)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${ROOT}/.tmp/crabline"
BIN="${PREFIX}/node_modules/.bin/crabline"
READY="${PREFIX}/ready/slack-server.json"
RECORDER="${PREFIX}/recorders/slack.jsonl"
HOST="${CRABLINE_HOST:-0.0.0.0}"
PORT="${CRABLINE_PORT:-8787}"

mkdir -p "${PREFIX}/ready" "${PREFIX}/recorders"

if [[ ! -x "${BIN}" ]]; then
  echo "Installing @openclaw/crabline into ${PREFIX}…"
  npm install --prefix "${PREFIX}" @openclaw/crabline@0.1.17
fi

# Stop previous serve on this port / ready-file lock
if lsof -tiTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Killing existing listener on :${PORT}"
  kill -9 $(lsof -tiTCP:"${PORT}" -sTCP:LISTEN) 2>/dev/null || true
  sleep 1
fi
rm -rf "${READY}.lock" "${READY}"

echo "Starting Crabline Slack on ${HOST}:${PORT}…"
nohup "${BIN}" --json serve slack \
  --host "${HOST}" \
  --port "${PORT}" \
  --ready-file "${READY}" \
  --recorder "${RECORDER}" \
  >"${PREFIX}/serve.out.log" 2>"${PREFIX}/serve.err.log" &
echo $! >"${PREFIX}/serve.pid"

for _ in $(seq 1 20); do
  if [[ -f "${READY}" ]]; then
    break
  fi
  sleep 0.25
done

if [[ ! -f "${READY}" ]]; then
  echo "error: ready file not written; see ${PREFIX}/serve.err.log" >&2
  exit 1
fi

python3 - "${READY}" "${PORT}" <<'PY'
import json, sys
from pathlib import Path
ready = Path(sys.argv[1])
port = sys.argv[2]
data = json.loads(ready.read_text())
host_api = f"http://host.openshell.internal:{port}/api/"
print("Crabline Slack ready")
print(f"  ready file:     {ready}")
print(f"  recorder:       {data.get('recorderPath')}")
print(f"  apiRoot (host): {data['endpoints']['apiRoot']}")
print(f"  apiRoot (sbx):  {host_api}")
print(f"  adminInbound:   {data['endpoints']['adminInboundUrl']}")
print(f"  botToken:       {data['botToken'][:12]}…")
print()
print("OpenClaw sandbox env (rewrite loopback → host.openshell.internal):")
print(f"  SLACK_API_URL={host_api}")
print(f"  SLACK_BOT_TOKEN={data['botToken']}")
print(f"  SLACK_SIGNING_SECRET={data['signingSecret']}")
PY
