#!/usr/bin/env bash
# AEH → OpenShell → COS agent (installed via claws add) → slash command skills.
# Uses the newer Quay image with OpenClaw 2026.8.1-beta.3.
#
# Prerequisites (leave running in other terminals):
#   ./examples/start-crabline-slack.sh
#   ./examples/start-smolclaw.sh
#   .eval-venv/bin/python examples/claude-vertex-proxy.py
#
# Usage:
#   ./examples/run-openclaw-forge-cos-agent.sh
#   ./examples/run-openclaw-forge-cos-agent.sh --cases daily-briefing
#   ./examples/run-openclaw-forge-cos-agent.sh --keep-sandbox
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
READY="${ROOT}/.tmp/crabline/ready/slack-server.json"
RECORDER="${ROOT}/.tmp/crabline/recorders/slack.jsonl"
SMOL_READY="${ROOT}/.tmp/smolclaw/ready.json"
EVAL_YAML="${ROOT}/eval/openclaw-forge-cos-agent/eval.yaml"
BOOTSTRAP="${ROOT}/examples/bootstrap-openclaw-forge-cos-agent.sh"
PY="${ROOT}/.eval-venv/bin/python"

if [[ ! -x "${PY}" ]]; then
  echo "error: missing ${PY} — create .eval-venv first" >&2
  exit 1
fi
if [[ ! -f "${READY}" ]]; then
  echo "error: missing ${READY} — run ./examples/start-crabline-slack.sh first" >&2
  exit 1
fi
if [[ ! -f "${SMOL_READY}" ]]; then
  echo "error: missing ${SMOL_READY} — run ./examples/start-smolclaw.sh first" >&2
  exit 1
fi
if ! curl -sf -m 2 http://127.0.0.1:8001/gmail/v1/users/me/profile >/dev/null; then
  echo "error: smolclaw Gmail not healthy on :8001" >&2
  exit 1
fi
if ! curl -sf -m 2 http://127.0.0.1:8002/calendar/v3/users/me/calendarList >/dev/null; then
  echo "error: smolclaw Calendar not healthy on :8002" >&2
  exit 1
fi
if [[ ! -f "${EVAL_YAML}" ]]; then
  echo "Bootstrapping eval package…"
  "${BOOTSTRAP}"
fi
if ! curl -sf -m 2 http://127.0.0.1:8000/health >/dev/null; then
  echo "error: nothing healthy on :8000 — start the Vertex proxy" >&2
  exit 1
fi

# Export bot token
eval "$("${PY}" - "${READY}" <<'PY'
import json, shlex, sys
from pathlib import Path
ready = json.loads(Path(sys.argv[1]).read_text())
print(f"export SLACK_BOT_TOKEN={shlex.quote(ready['botToken'])}")
PY
)"

export CRABLINE_RECORDER="${RECORDER}"
export CRABLINE_READY_FILE="${READY}"
export CRABLINE_API_URL="${CRABLINE_API_URL:-http://127.0.0.1:8787/api/}"
export SMOLCLAW_GMAIL_URL="${SMOLCLAW_GMAIL_URL:-http://127.0.0.1:8001/gmail/v1/}"
export SMOLCLAW_GCAL_URL="${SMOLCLAW_GCAL_URL:-http://127.0.0.1:8002/calendar/v3/}"
export OPENSHELL_GATEWAY_ENDPOINT="${OPENSHELL_GATEWAY_ENDPOINT:-https://127.0.0.1:17670}"
export AGENT_EVAL_OPENSHELL_IMAGE="${AGENT_EVAL_OPENSHELL_IMAGE:-quay.io/aipcc/base-images/agentic/openclaw:0.0.1-1787752534}"
export AGENT_EVAL_OPENSHELL_POLICY="${AGENT_EVAL_OPENSHELL_POLICY:-${ROOT}/deploy/openshell/eval-policy.yaml}"
export AGENT_EVAL_OPENSHELL_PROVIDER="${AGENT_EVAL_OPENSHELL_PROVIDER:-inference}"
export AGENT_EVAL_RUNS_DIR="${FORGE_COS_AGENT_RUNS_DIR:-${ROOT}/eval/openclaw-forge-cos-agent/eval/runs}"

RUN_ID="${RUN_ID:-forge-cos-agent-$(date +%Y%m%d-%H%M%S)}"
MODEL="${MODEL:-inference/claude-opus-4-6}"

echo "SLACK_BOT_TOKEN set; recorder=${CRABLINE_RECORDER}"
echo "smolclaw gmail=${SMOLCLAW_GMAIL_URL} gcal=${SMOLCLAW_GCAL_URL}"
echo "RUN_ID=${RUN_ID} model=${MODEL}"
echo "image=${AGENT_EVAL_OPENSHELL_IMAGE}"
echo "stack: AEH → OpenShell → COS agent (claws add) → /skill-name"

cd "${ROOT}"
"${PY}" -m agent_eval.openshell.run \
  --config "${EVAL_YAML}" \
  --model "${MODEL}" \
  --run-id "${RUN_ID}" \
  "$@" \
  2>&1 | tee "${ROOT}/.tmp/aeh-${RUN_ID}.log"

echo
echo "Report: ${AGENT_EVAL_RUNS_DIR}/forge-cos-agent/${RUN_ID}/report.html"
echo "Summary: ${AGENT_EVAL_RUNS_DIR}/forge-cos-agent/${RUN_ID}/summary.yaml"
