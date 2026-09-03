#!/usr/bin/env bash
# AEH → Harbor (Podman) → OpenClaw → real M365 (Forge morning-briefing + analysis-panel).
#
# Same eval pack as the OpenShell path (eval/openclaw-forge-agent), but gateway-free:
# OpenClaw talks to a host OpenAI-compatible proxy via host.containers.internal
# instead of OpenShell inference.local.
#
# Prerequisites:
#   1) .tmp/forge-real/env with M365_ACCESS_TOKEN (examples/forge-real/device_login.sh)
#   2) Seeded mailbox (examples/forge-real/seed_m365_graph.py)
#   3) Vertex/OpenAI-compatible proxy on :8000 (examples/claude-vertex-proxy.py)
#   4) Harbor OpenClaw image built:
#        podman build --platform linux/amd64 \
#          -f deploy/harbor/Containerfile.openclaw \
#          -t localhost/agent-eval-openclaw:latest .
#
# Usage:
#   ./examples/run-openclaw-forge-agent-harbor-eval.sh
#   ./examples/run-openclaw-forge-agent-harbor-eval.sh --cases morning-briefing
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${FORGE_REAL_ENV:-${ROOT}/.tmp/forge-real/env}"
EVAL_YAML="${ROOT}/eval/openclaw-forge-agent/eval.yaml"
PY="${ROOT}/.eval-venv/bin/python"
IMAGE="${AGENT_EVAL_HARBOR_OPENCLAW_IMAGE:-localhost/agent-eval-openclaw:latest}"
PROXY_URL="${OPENCLAW_INFERENCE_BASE_URL:-http://host.containers.internal:8000/v1}"

if [[ ! -x "${PY}" ]]; then
  echo "error: missing ${PY} — create .eval-venv first" >&2
  exit 1
fi
if [[ ! -f "${EVAL_YAML}" ]]; then
  echo "error: missing ${EVAL_YAML}" >&2
  exit 1
fi
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

if [[ -z "${M365_ACCESS_TOKEN:-}" && -f "${ROOT}/.tmp/forge-real/m365-token.json" ]]; then
  M365_ACCESS_TOKEN="$("${PY}" -c 'import json;print(json.load(open(".tmp/forge-real/m365-token.json"))["access_token"])')"
  export M365_ACCESS_TOKEN
fi
: "${M365_ACCESS_TOKEN:?Set M365_ACCESS_TOKEN — run examples/forge-real/device_login.sh}"
export M365_USER="${M365_USER:-tbx-demo2@dev.mscloud.ibm.com}"
export FORGE_SOURCES="${FORGE_SOURCES:-m365-only}"

# Host-side Graph smoke (same gate as the OpenShell wrapper).
if ! curl -fsS --noproxy '*' -m 20 \
  -H "Authorization: Bearer ${M365_ACCESS_TOKEN}" \
  "https://graph.microsoft.com/v1.0/me?\$select=userPrincipalName,mail" \
  | grep -Eqi 'userPrincipalName|"mail"'; then
  echo "error: Graph /me failed — refresh token via device_login.sh" >&2
  exit 1
fi
echo "m365: Graph /me ok (user=${M365_USER})"

# OpenClaw provider → host proxy (Harbor has no inference.local).
export OPENCLAW_INFERENCE_BASE_URL="${PROXY_URL}"
export OPENCLAW_INFERENCE_API_KEY="${OPENCLAW_INFERENCE_API_KEY:-empty}"
export OPENCLAW_INFERENCE_MODEL="${OPENCLAW_INFERENCE_MODEL:-claude-sonnet-4}"
# Judges / reward bridge: use the host Anthropic-messages shim, not Vertex.
# score.py prefers AnthropicVertex whenever ANTHROPIC_VERTEX_PROJECT_ID is set;
# that path needs google-auth inside the trial image and bypasses the shim.
unset ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION GOOGLE_CLOUD_PROJECT \
  CLAUDE_CODE_USE_VERTEX GCP_SA_ACCESS_TOKEN || true
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-http://host.containers.internal:8001}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-shim-local}"

if ! curl -sf --noproxy '*' -m 3 "http://127.0.0.1:8000/health" >/dev/null \
  && ! curl -sf --noproxy '*' -m 3 "http://127.0.0.1:8000/v1/models" >/dev/null; then
  echo "warning: host proxy on :8000 not responding — start examples/claude-vertex-proxy.py" >&2
fi

# Judges need the Anthropic-messages shim on :8001 (not just /health).
if ! curl -sf --noproxy '*' -m 5 "http://127.0.0.1:8001/v1/messages" \
    -H 'content-type: application/json' -H 'x-api-key: shim-local' \
    -H 'anthropic-version: 2023-06-01' \
    -d '{"model":"claude-sonnet-4","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}' >/dev/null; then
  echo "warning: Anthropic shim on :8001 /v1/messages not responding — start examples/anthropic-messages-shim.py (HOST=0.0.0.0)" >&2
fi

RUN_ID="${RUN_ID:-forge-harbor-$(date +%Y%m%d-%H%M%S)}"
MODEL="${MODEL:-inference/claude-sonnet-4}"
export AGENT_EVAL_RUNS_DIR="${FORGE_AGENT_EVAL_RUNS_DIR:-${ROOT}/eval/openclaw-forge-agent/eval/runs}"
OUT_DIR="${AGENT_EVAL_RUNS_DIR}/forge-eval-rubrics/${RUN_ID}"
TASKS_DIR="${ROOT}/.tmp/harbor-tasks/openclaw-forge-agent/${RUN_ID}"
JOBS_DIR="${ROOT}/.tmp/harbor-jobs/openclaw-forge-agent/${RUN_ID}"

echo "RUN_ID=${RUN_ID} model=${MODEL}"
echo "image=${IMAGE}"
echo "stack: AEH → Harbor/Podman → OpenClaw → ${OPENCLAW_INFERENCE_BASE_URL} + M365 Graph"
echo "agent: agent_eval.harbor.agents.openclaw:OpenClawAgent"

cd "${ROOT}"
mkdir -p "${ROOT}/.tmp" "${OUT_DIR}" "${TASKS_DIR}" "${JOBS_DIR}"

PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
"${PY}" -m agent_eval.harbor.run \
  --config "${EVAL_YAML}" \
  --model "${MODEL}" \
  --image "${IMAGE}" \
  --env podman \
  --output "${OUT_DIR}" \
  --tasks-dir "${TASKS_DIR}" \
  --jobs-dir "${JOBS_DIR}" \
  --regenerate \
  "$@" \
  2>&1 | tee "${ROOT}/.tmp/aeh-harbor-${RUN_ID}.log"

echo
echo "Report under: ${OUT_DIR}/"
