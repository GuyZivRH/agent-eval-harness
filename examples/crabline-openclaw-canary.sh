#!/usr/bin/env bash
# Phase-1 canary: Quay OpenClaw sandbox → host Crabline Slack Web API.
#
# Proves: OpenShell policy + host.openshell.internal:8787 + Crabline chat.postMessage.
# Does NOT start OpenClaw gateway Slack channel (Quay 2026.7.2-beta.7 segfaults with
# channels.slack enabled under OpenShell — see docs/openshell-openclaw-crabline-agent.md).
#
# Prerequisites:
#   ./examples/start-crabline-slack.sh   # leave running
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
READY="${ROOT}/.tmp/crabline/ready/slack-server.json"
POLICY="${ROOT}/deploy/openshell/eval-policy.yaml"
IMAGE="${AGENT_EVAL_OPENSHELL_IMAGE:-quay.io/aipcc/base-images/agentic/openclaw:latest}"
SB="${CRABLINE_SANDBOX_NAME:-ccanary1}"

unset OPENSHELL_GATEWAY_ENDPOINT || true
export OPENSHELL_GATEWAY="${OPENSHELL_GATEWAY:-openshell}"

if [[ ! -f "${READY}" ]]; then
  echo "error: missing ${READY} — run ./examples/start-crabline-slack.sh first" >&2
  exit 1
fi

python3 - "${READY}" "${ROOT}/.tmp/crabline/canary-env.sh" <<'PY'
import json, sys
from pathlib import Path
ready = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(
    "\n".join(
        [
            "export SLACK_API_URL=http://host.openshell.internal:8787/api/",
            f"export SLACK_BOT_TOKEN={ready['botToken']}",
        ]
    )
    + "\n"
)
print("loaded", sys.argv[2])
PY
# shellcheck disable=SC1091
source "${ROOT}/.tmp/crabline/canary-env.sh"

echo "=== ensure sandbox ${SB} ==="
if ! openshell sandbox list 2>/dev/null | grep -q "${SB}"; then
  openshell sandbox create --name "${SB}" \
    --from "${IMAGE}" \
    --policy "${POLICY}" \
    --provider inference \
    --no-tty --no-auto-providers -- echo ready
fi

REC="${ROOT}/.tmp/crabline/recorders/slack.jsonl"

echo "=== sandbox → Crabline auth.test + conversations.open + chat.postMessage ==="
openshell sandbox exec -n "${SB}" --workdir /sandbox -- \
  env SLACK_API_URL="${SLACK_API_URL}" SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
  sh -c '
set -e
echo "auth.test:"
curl -sS -m 5 -X POST "${SLACK_API_URL}auth.test" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded"
echo
echo "conversations.open:"
OPEN_JSON=$(curl -sS -m 5 -X POST "${SLACK_API_URL}conversations.open" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "users=UCANARY01")
echo "$OPEN_JSON"
CHANNEL=$(printf "%s" "$OPEN_JSON" | sed -n "s/.*\"id\":\"\\([CDG][A-Z0-9]*\\)\".*/\\1/p" | head -1)
if [ -z "$CHANNEL" ]; then
  echo "failed to parse DM channel id" >&2
  exit 1
fi
echo "channel=$CHANNEL"
echo "chat.postMessage:"
curl -sS -m 5 -X POST "${SLACK_API_URL}chat.postMessage" \
  -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "channel=${CHANNEL}" \
  --data-urlencode "text=pong from quay sandbox"
echo
'

echo "=== host recorder should show accepted chat.postMessage ==="
sleep 1
if grep -E '"path":"/api/chat.postMessage".*"accepted":true|"accepted":true.*"path":"/api/chat.postMessage"' "${REC}" >/dev/null 2>&1 \
  || grep -F '"path":"/api/chat.postMessage"' "${REC}" | grep -q '"accepted":true'; then
  echo "PASS: Crabline recorded accepted chat.postMessage"
  grep -F 'chat.postMessage' "${REC}" | tail -10
  exit 0
fi
echo "FAIL: no accepted chat.postMessage in ${REC}"
tail -20 "${REC}" 2>/dev/null || true
exit 1
