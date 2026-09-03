#!/usr/bin/env bash
# AEH → OpenShell → Quay OpenClaw agent → host Crabline + smolclaw.
# Forge evaluation rubrics — scene-based seeding, LLM rubric judges.
#
# Prerequisites (leave running in other terminals):
#   ./examples/start-crabline-slack.sh
#   ./examples/start-smolclaw.sh
#   .eval-venv/bin/python examples/claude-vertex-proxy.py   # :8000 → inference.local
#
# Usage:
#   ./examples/run-openclaw-forge-agent-eval.sh
#   ./examples/run-openclaw-forge-agent-eval.sh --cases daily-briefing
#   ./examples/run-openclaw-forge-agent-eval.sh --keep-sandbox
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
READY="${ROOT}/.tmp/crabline/ready/slack-server.json"
RECORDER="${ROOT}/.tmp/crabline/recorders/slack.jsonl"
SMOL_READY="${ROOT}/.tmp/smolclaw/ready.json"
EVAL_YAML="${ROOT}/eval/openclaw-forge-agent/eval.yaml"
BOOTSTRAP="${ROOT}/examples/bootstrap-openclaw-forge-agent-eval.sh"
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
  echo "error: smolclaw Gmail not healthy on :8001 — run ./examples/start-smolclaw.sh" >&2
  exit 1
fi
if ! curl -sf -m 2 http://127.0.0.1:8002/calendar/v3/users/me/calendarList >/dev/null; then
  echo "error: smolclaw Calendar not healthy on :8002 — run ./examples/start-smolclaw.sh" >&2
  exit 1
fi
if [[ ! -f "${EVAL_YAML}" ]]; then
  echo "Bootstrapping eval package (missing ${EVAL_YAML})…"
  "${BOOTSTRAP}"
fi

# Fail fast if :8000 is not a tool-aware Vertex proxy.
if ! curl -sf -m 2 http://127.0.0.1:8000/health >/dev/null; then
  echo "error: nothing healthy on :8000 — start:" >&2
  echo "  .eval-venv/bin/python examples/claude-vertex-proxy.py" >&2
  exit 1
fi
PROXY_PID="$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
PROXY_CMD=""
if [[ -n "${PROXY_PID}" ]]; then
  PROXY_CMD="$(ps -p "${PROXY_PID}" -o command= 2>/dev/null || true)"
fi
# /tmp/claude_proxy.py is often auto-started by the local stack; it is OK only
# if it is a copy of the tool-aware examples/claude-vertex-proxy.py.
if printf '%s' "${PROXY_CMD}" | grep -q '/tmp/claude_proxy.py'; then
  if ! grep -q '_openai_tools_to_anthropic' /tmp/claude_proxy.py 2>/dev/null; then
    echo "error: :8000 is old /tmp/claude_proxy.py (no tool calling)." >&2
    echo "  Fix:" >&2
    echo "    kill ${PROXY_PID}" >&2
    echo "    cp examples/claude-vertex-proxy.py /tmp/claude_proxy.py" >&2
    echo "    .eval-venv/bin/python /tmp/claude_proxy.py" >&2
    exit 1
  fi
  echo "proxy: /tmp/claude_proxy.py (tool-aware) pid=${PROXY_PID}"
elif ! printf '%s' "${PROXY_CMD}" | grep -q 'claude-vertex-proxy.py'; then
  echo "warning: :8000 may not be the Vertex proxy:" >&2
  echo "  pid=${PROXY_PID} cmd=${PROXY_CMD}" >&2
else
  echo "proxy: claude-vertex-proxy.py pid=${PROXY_PID}"
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
export CRABLINE_READY_FILE="${READY}"
export CRABLINE_API_URL="${CRABLINE_API_URL:-http://127.0.0.1:8787/api/}"
export SMOLCLAW_GMAIL_URL="${SMOLCLAW_GMAIL_URL:-http://127.0.0.1:8001/gmail/v1/}"
export SMOLCLAW_GCAL_URL="${SMOLCLAW_GCAL_URL:-http://127.0.0.1:8002/calendar/v3/}"
export OPENSHELL_GATEWAY_ENDPOINT="${OPENSHELL_GATEWAY_ENDPOINT:-https://127.0.0.1:17670}"
export AGENT_EVAL_OPENSHELL_IMAGE="${AGENT_EVAL_OPENSHELL_IMAGE:-quay.io/aipcc/base-images/agentic/openclaw:latest}"
export AGENT_EVAL_OPENSHELL_POLICY="${AGENT_EVAL_OPENSHELL_POLICY:-${ROOT}/deploy/openshell/eval-policy.yaml}"
export AGENT_EVAL_OPENSHELL_PROVIDER="${AGENT_EVAL_OPENSHELL_PROVIDER:-inference}"
export AGENT_EVAL_RUNS_DIR="${FORGE_AGENT_EVAL_RUNS_DIR:-${ROOT}/eval/openclaw-forge-agent/eval/runs}"

RUN_ID="${RUN_ID:-forge-agent-$(date +%Y%m%d-%H%M%S)}"
MODEL="${MODEL:-inference/claude-sonnet-4}"

echo "SLACK_BOT_TOKEN set; recorder=${CRABLINE_RECORDER}"
echo "smolclaw gmail=${SMOLCLAW_GMAIL_URL} gcal=${SMOLCLAW_GCAL_URL}"
echo "RUN_ID=${RUN_ID} model=${MODEL}"
echo "stack: AEH → OpenShell → Quay OpenClaw → Crabline + smolclaw (Forge rubrics)"

cd "${ROOT}"
"${PY}" -m agent_eval.openshell.run \
  --config "${EVAL_YAML}" \
  --model "${MODEL}" \
  --run-id "${RUN_ID}" \
  "$@" \
  2>&1 | tee "${ROOT}/.tmp/aeh-${RUN_ID}.log"

echo
echo "Report: ${AGENT_EVAL_RUNS_DIR}/forge-eval-rubrics/${RUN_ID}/report.html"
echo "Summary: ${AGENT_EVAL_RUNS_DIR}/forge-eval-rubrics/${RUN_ID}/summary.yaml"
