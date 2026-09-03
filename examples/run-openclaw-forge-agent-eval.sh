#!/usr/bin/env bash
# AEH → OpenShell → Quay OpenClaw 7.2 → real M365 only (tbx-demo2).
# Forge evaluation rubrics — Slack deferred until ibm-forge-demo bot token.
#
# Prerequisites:
#   1) .tmp/forge-real/env with M365_ACCESS_TOKEN (device_login.sh)
#   2) .eval-venv/bin/python examples/forge-real/seed_m365_graph.py  # already done
#   3) .eval-venv/bin/python examples/claude-vertex-proxy.py         # :8000
#
# Usage:
#   ./examples/run-openclaw-forge-agent-eval.sh
#   ./examples/run-openclaw-forge-agent-eval.sh --cases morning-briefing
#   FORGE_SOURCES=m365-only ./examples/run-openclaw-forge-agent-eval.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${FORGE_REAL_ENV:-${ROOT}/.tmp/forge-real/env}"
EVAL_YAML="${ROOT}/eval/openclaw-forge-agent/eval.yaml"
PY="${ROOT}/.eval-venv/bin/python"
# redhat-et csb-openclaw-only-* fails OpenShell: "OCI USER '1001' resolves to prohibited primary GID 0"
# aipcc latest is OpenClaw 2026.7.2-beta.7 and provisions cleanly.
# 8.1 trial image (aipcc; uid/gid 1001 — OpenShell-safe, unlike redhat-et CSB):
IMG_8_1="quay.io/aipcc/base-images/agentic/openclaw:2026.8.1-beta.3"
IMG_DEFAULT="quay.io/aipcc/base-images/agentic/openclaw:latest"

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

# Prefer token file if env export missing/stale
if [[ -z "${M365_ACCESS_TOKEN:-}" && -f "${ROOT}/.tmp/forge-real/m365-token.json" ]]; then
  M365_ACCESS_TOKEN="$("${PY}" -c 'import json;print(json.load(open(".tmp/forge-real/m365-token.json"))["access_token"])')"
  export M365_ACCESS_TOKEN
fi

: "${M365_ACCESS_TOKEN:?Set M365_ACCESS_TOKEN — run ./examples/forge-real/device_login.sh as tbx-demo2}"
export M365_USER="${M365_USER:-tbx-demo2@dev.mscloud.ibm.com}"
export FORGE_SOURCES="${FORGE_SOURCES:-m365-only}"

# Resolve 8.1 allow AFTER sourcing env so CLI + env aliases both work.
_ALLOW_8_1="${ALLOW_OPENCLAW_8_1:-${ALLOW_OPENCLAW_8_1:-0}}"
if [[ "${_ALLOW_8_1}" == "1" ]]; then
  IMG_DEFAULT="${IMG_8_1}"
fi


# Host-side Graph smoke
if ! curl -fsS --noproxy '*' -m 20 \
  -H "Authorization: Bearer ${M365_ACCESS_TOKEN}" \
  "https://graph.microsoft.com/v1.0/me?\$select=userPrincipalName,mail" \
  | grep -Eqi 'userPrincipalName|"mail"'; then
  echo "error: Graph /me failed — refresh token via device_login.sh" >&2
  exit 1
fi
echo "m365: Graph /me ok (user=${M365_USER})"

MAIL_N="$(curl -fsS --noproxy '*' -m 20 \
  -H "Authorization: Bearer ${M365_ACCESS_TOKEN}" \
  "https://graph.microsoft.com/v1.0/me/messages?\$top=1&\$select=id" \
  | "${PY}" -c 'import sys,json; print(len(json.load(sys.stdin).get("value") or []))')"
CAL_N="$(curl -fsS --noproxy '*' -m 20 \
  -H "Authorization: Bearer ${M365_ACCESS_TOKEN}" \
  "https://graph.microsoft.com/v1.0/me/calendar/events?\$top=1&\$select=id" \
  | "${PY}" -c 'import sys,json; print(len(json.load(sys.stdin).get("value") or []))')"
if [[ "${MAIL_N}" -lt 1 || "${CAL_N}" -lt 1 ]]; then
  echo "error: mailbox or calendar empty — re-run seed_m365_graph.py" >&2
  exit 1
fi
echo "m365: mail/calendar non-empty"

if ! curl -sf --noproxy '*' -m 10 http://127.0.0.1:8000/health >/dev/null; then
  echo "error: nothing healthy on :8000 — start:" >&2
  echo "  .eval-venv/bin/python examples/claude-vertex-proxy.py" >&2
  exit 1
fi
echo "proxy: :8000 healthy (agent inference)"

# Judges: host ADC → Vertex is IAM-denied; route Anthropic Messages via shim → :8000.
SHIM_PORT="${FORGE_JUDGE_SHIM_PORT:-8001}"
SHIM_URL="http://127.0.0.1:${SHIM_PORT}"
if ! curl -sf --noproxy '*' -m 3 "${SHIM_URL}/health" >/dev/null; then
  echo "judge-shim: starting on :${SHIM_PORT} (Anthropic /v1/messages → :8000)"
  mkdir -p "${ROOT}/.tmp"
  SHIM_PY="${ROOT}/examples/anthropic-messages-shim.py"
  if [[ ! -f "${SHIM_PY}" ]]; then
    SHIM_PY="${ROOT}/.tmp/anthropic-messages-shim.py"
  fi
  if [[ ! -f "${SHIM_PY}" ]]; then
    echo "error: missing anthropic-messages-shim.py under examples/ or .tmp/" >&2
    exit 1
  fi
  nohup env PORT="${SHIM_PORT}" "${PY}" "${SHIM_PY}" \
    >"${ROOT}/.tmp/judge-shim.log" 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -sf --noproxy '*' -m 1 "${SHIM_URL}/health" >/dev/null && break
    sleep 0.3
  done
fi
if ! curl -sf --noproxy '*' -m 3 "${SHIM_URL}/health" >/dev/null; then
  echo "error: judge shim not healthy on ${SHIM_URL}" >&2
  exit 1
fi
echo "judge-shim: ${SHIM_URL} healthy"

# Force score.py onto Anthropic(base_url=shim), not AnthropicVertex(ADC).
unset ANTHROPIC_VERTEX_PROJECT_ID ANTHROPIC_VERTEX_REGION CLOUD_ML_REGION || true
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-${SHIM_URL}}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-shim-local}"
# Alias that the :8000 proxy MODEL_MAP understands (dated Vertex ids also remapped by shim).
export EVAL_JUDGE_MODEL="${EVAL_JUDGE_MODEL:-claude-sonnet-4}"

# Must match `openshell gateway list` (registered as localhost, not 127.0.0.1)
export OPENSHELL_GATEWAY_ENDPOINT="${OPENSHELL_GATEWAY_ENDPOINT:-https://localhost:17670}"
# .tmp/forge-real/env often pins AGENT_EVAL_OPENSHELL_IMAGE=...:latest (7.2).
# When an 8.1 trial is explicitly requested, do not let that silent pin win
# unless the caller already selected an 8.1 tag.
if [[ "${_ALLOW_8_1}" == "1" ]]; then
  if ! printf '%s' "${AGENT_EVAL_OPENSHELL_IMAGE:-}" | grep -qE '8\.1|2026\.8\.1'; then
    export AGENT_EVAL_OPENSHELL_IMAGE="${IMG_8_1}"
    echo "note: ALLOW_OPENCLAW_8_1=1 forcing image ${IMG_8_1} (overriding env :latest pin if any)"
  fi
fi
export AGENT_EVAL_OPENSHELL_IMAGE="${AGENT_EVAL_OPENSHELL_IMAGE:-${IMG_DEFAULT}}"
export AGENT_EVAL_OPENSHELL_POLICY="${AGENT_EVAL_OPENSHELL_POLICY:-${ROOT}/deploy/openshell/eval-policy.yaml}"
export AGENT_EVAL_OPENSHELL_PROVIDER="${AGENT_EVAL_OPENSHELL_PROVIDER:-inference}"
export AGENT_EVAL_RUNS_DIR="${FORGE_AGENT_EVAL_RUNS_DIR:-${ROOT}/eval/openclaw-forge-agent/eval/runs}"

if printf '%s' "${AGENT_EVAL_OPENSHELL_IMAGE}" | grep -qE '8\.1|2026\.8\.1'; then
  if [[ "${_ALLOW_8_1}" != "1" ]]; then
    echo "error: refuse OpenClaw 8.1 unless ALLOW_OPENCLAW_8_1=1 (default pin: quay.io/aipcc/.../openclaw:latest)" >&2
    exit 1
  fi
  echo "warning: ALLOW_OPENCLAW_8_1=1 — using OpenClaw 8.1 image for this trial"
fi

RUN_ID="${RUN_ID:-forge-m365-$(date +%Y%m%d-%H%M%S)}"
MODEL="${MODEL:-inference/claude-sonnet-4}"

OC_LABEL="7.2"
if printf '%s' "${AGENT_EVAL_OPENSHELL_IMAGE}" | grep -qE '8\.1|2026\.8\.1'; then
  OC_LABEL="8.1"
fi

echo "RUN_ID=${RUN_ID} model=${MODEL} judge=${EVAL_JUDGE_MODEL}"
echo "image=${AGENT_EVAL_OPENSHELL_IMAGE}"
echo "stack: AEH → OpenShell → OpenClaw ${OC_LABEL} → M365 Graph only (Slack deferred)"
echo "judges: via ${ANTHROPIC_BASE_URL} (not direct Vertex ADC)"

cd "${ROOT}"
mkdir -p "${ROOT}/.tmp"
"${PY}" -m agent_eval.openshell.run \
  --config "${EVAL_YAML}" \
  --model "${MODEL}" \
  --run-id "${RUN_ID}" \
  "$@" \
  2>&1 | tee "${ROOT}/.tmp/aeh-${RUN_ID}.log"

echo
echo "Report: ${AGENT_EVAL_RUNS_DIR}/forge-eval-rubrics/${RUN_ID}/report.html"
echo "Summary: ${AGENT_EVAL_RUNS_DIR}/forge-eval-rubrics/${RUN_ID}/summary.yaml"
