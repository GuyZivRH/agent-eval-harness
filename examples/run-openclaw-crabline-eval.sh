#!/usr/bin/env bash
# Phase 1.5: AEH OpenShell CLI case → Crabline, scored by recorder judge.
#
# Prerequisites (leave Crabline running in another terminal):
#   ./examples/start-crabline-slack.sh
#
# Usage:
#   ./examples/run-openclaw-crabline-eval.sh
#   ./examples/run-openclaw-crabline-eval.sh --keep-sandbox
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
READY="${ROOT}/.tmp/crabline/ready/slack-server.json"
RECORDER="${ROOT}/.tmp/crabline/recorders/slack.jsonl"
EVAL_YAML="${ROOT}/eval/openclaw-crabline/eval.yaml"
PY="${ROOT}/.eval-venv/bin/python"

if [[ ! -x "${PY}" ]]; then
  echo "error: missing ${PY} — create .eval-venv first" >&2
  exit 1
fi
if [[ ! -f "${READY}" ]]; then
  echo "error: missing ${READY} — run ./examples/start-crabline-slack.sh first" >&2
  exit 1
fi

# Export bot token for execution.env: $SLACK_BOT_TOKEN
eval "$("${PY}" - "${READY}" <<'PY'
import json, shlex, sys
from pathlib import Path
ready = json.loads(Path(sys.argv[1]).read_text())
print(f"export SLACK_BOT_TOKEN={shlex.quote(ready['botToken'])}")
PY
)"

export CRABLINE_RECORDER="${RECORDER}"
export OPENSHELL_GATEWAY_ENDPOINT="${OPENSHELL_GATEWAY_ENDPOINT:-https://localhost:17670}"
export AGENT_EVAL_OPENSHELL_IMAGE="${AGENT_EVAL_OPENSHELL_IMAGE:-quay.io/aipcc/base-images/agentic/openclaw:latest}"
export AGENT_EVAL_OPENSHELL_POLICY="${AGENT_EVAL_OPENSHELL_POLICY:-${ROOT}/deploy/openshell/eval-policy.yaml}"
export AGENT_EVAL_OPENSHELL_PROVIDER="${AGENT_EVAL_OPENSHELL_PROVIDER:-inference}"
# Always write under this eval (ignore leftover AGENT_EVAL_RUNS_DIR from other demos)
export AGENT_EVAL_RUNS_DIR="${CRABLINE_EVAL_RUNS_DIR:-${ROOT}/eval/openclaw-crabline/eval/runs}"

RUN_ID="${RUN_ID:-crabline-aeh-$(date +%Y%m%d-%H%M%S)}"

echo "SLACK_BOT_TOKEN set; recorder=${CRABLINE_RECORDER}"
echo "RUN_ID=${RUN_ID}"

cd "${ROOT}"
"${PY}" -m agent_eval.openshell.run \
  --config "${EVAL_YAML}" \
  --model cli \
  --run-id "${RUN_ID}" \
  --no-llm-judges \
  "$@" \
  2>&1 | tee "${ROOT}/.tmp/aeh-${RUN_ID}.log"

echo
echo "Report: ${AGENT_EVAL_RUNS_DIR}/openclaw-crabline/${RUN_ID}/report.html"
echo "Summary: ${AGENT_EVAL_RUNS_DIR}/openclaw-crabline/${RUN_ID}/summary.yaml"
