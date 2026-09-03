#!/usr/bin/env bash
# Smoke: from an OpenShell sandbox on the 7.2 image, hit Slack + Graph + Vertex.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${FORGE_REAL_ENV:-${ROOT}/.tmp/forge-real/env}"
IMG="${AGENT_EVAL_OPENSHELL_IMAGE:-quay.io/aipcc/base-images/agentic/openclaw:latest}"
POLICY="${AGENT_EVAL_OPENSHELL_POLICY:-${ROOT}/deploy/openshell/eval-policy.yaml}"
SB="${FORGE_SMOKE_SANDBOX:-forge-real-smoke}"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
fi
: "${SLACK_BOT_TOKEN:?}"
: "${M365_ACCESS_TOKEN:?}"

export OPENSHELL_GATEWAY="${OPENSHELL_GATEWAY:-openshell}"
unset OPENSHELL_GATEWAY_ENDPOINT || true

echo "recreate sandbox ${SB} on ${IMG}"
openshell sandbox delete "${SB}" 2>/dev/null || true
openshell sandbox create \
  --name "${SB}" \
  --image "${IMG}" \
  --policy "${POLICY}" \
  --from-provider inference \
  -- env \
    SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
    M365_ACCESS_TOKEN="${M365_ACCESS_TOKEN}" \
    SLACK_API_URL="${SLACK_API_URL:-https://slack.com/api/}" \
    sleep infinity

openshell sandbox exec -n "${SB}" --workdir /sandbox -- sh -c '
set -e
echo "=== slack auth.test ==="
curl -fsS --noproxy "*" -m 20 -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  "${SLACK_API_URL%/}/auth.test" | head -c 400; echo
echo "=== graph /me ==="
curl -fsS --noproxy "*" -m 20 -H "Authorization: Bearer ${M365_ACCESS_TOKEN}" \
  "https://graph.microsoft.com/v1.0/me?\$select=userPrincipalName,mail" | head -c 400; echo
echo "=== openclaw version ==="
openclaw --version || true
echo "=== one-shot agent (list one slack channel name + one mail subject) ==="
openclaw agent --local --trust --json -m "Using curl + the tokens in env, list one Slack channel name from conversations.list and one email subject from Graph /me/messages. Reply with just those two lines." \
  2>/tmp/agent.err | head -c 2000
echo
echo "agent stderr:"; head -c 800 /tmp/agent.err || true
'
echo "smoke done"
