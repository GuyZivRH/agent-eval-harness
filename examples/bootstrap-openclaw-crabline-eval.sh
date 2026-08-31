#!/usr/bin/env bash
# Create eval/openclaw-crabline/ (Phase 1.5 CLI → Crabline, no OpenClaw agent).
# Idempotent: overwrites the demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-crabline"

mkdir -p "${EVAL_DIR}/cases/case-001"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# Phase-1.5: AEH scores Crabline Slack recorder after a Quay sandbox posts.
# Does NOT use OpenClaw gateway Slack (segfault on Quay 2026.7.2-beta.7).
# Runner is CLI curl inside the OpenShell sandbox → host Crabline.
name: openclaw-crabline

runner:
  type: cli
  # Case upload nests at /sandbox/{case_id}/; {args} = marker text
  command: >
    /bin/sh /sandbox/{case_id}/post-to-crabline.sh '{args}'

execution:
  mode: case
  prompt: "{{ input.marker_text }}"
  timeout: 120
  env:
    SLACK_API_URL: "http://host.openshell.internal:8787/api/"
    SLACK_BOT_TOKEN: "$SLACK_BOT_TOKEN"

dataset:
  path: cases
  workspace:
    files:
      - post-to-crabline.sh
  schema: |
    Each case has:
    - input.yaml: marker_text (unique string embedded in chat.postMessage)
    - annotations.yaml: expected_text (same marker for the Crabline recorder judge)
    - post-to-crabline.sh: sandbox script (conversations.open + chat.postMessage)

outputs:
  - path: output
    schema: |
      response.txt: sandbox script stdout / API summary
      crabline-hits.jsonl: matching recorder lines (written by the judge)

judges:
  - name: crabline_accepted_post
    module: agent_eval.openshell.crabline_score
    function: score_accepted_post_message
    feedback_type: bool

  - name: response_received
    check: |
      response = outputs.get("output_content", "") or ""
      return "ok" in response.lower() and "chat.postMessage" in response
    feedback_type: bool

thresholds:
  crabline_accepted_post:
    min_pass_rate: 1.0
  response_received:
    min_pass_rate: 1.0
EOF

cat > "${EVAL_DIR}/cases/case-001/input.yaml" <<'EOF'
marker_text: "aeh-crabline-case-001-marker"
EOF
cat > "${EVAL_DIR}/cases/case-001/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-case-001-marker"
EOF

cat > "${EVAL_DIR}/cases/case-001/post-to-crabline.sh" <<'EOF'
#!/bin/sh
# Post a unique marker to host Crabline Slack from inside an OpenShell sandbox.
# Args: $1 = message text (AEH marker)
set -eu

TEXT="${1:?marker text required}"
API="${SLACK_API_URL:?SLACK_API_URL required}"
TOKEN="${SLACK_BOT_TOKEN:?SLACK_BOT_TOKEN required}"

auth=$(curl -sS -m 15 -X POST "${API}auth.test" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded")
echo "auth.test: ${auth}"

open_json=$(curl -sS -m 15 -X POST "${API}conversations.open" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "users=UCANARY01")
echo "conversations.open: ${open_json}"

channel=$(printf '%s' "${open_json}" | node -e '
  let s = "";
  process.stdin.on("data", (c) => (s += c));
  process.stdin.on("end", () => {
    const j = JSON.parse(s);
    if (!j.ok || !j.channel || !j.channel.id) {
      console.error("conversations.open failed");
      process.exit(1);
    }
    process.stdout.write(j.channel.id);
  });
')

post=$(curl -sS -m 15 -X POST "${API}chat.postMessage" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "channel=${channel}" \
  --data-urlencode "text=${TEXT}")
echo "chat.postMessage: ${post}"

mkdir -p /sandbox/output
{
  echo "ok"
  echo "channel=${channel}"
  echo "chat.postMessage=${post}"
} > /sandbox/output/response.txt

printf '%s' "${post}" | node -e '
  let s = "";
  process.stdin.on("data", (c) => (s += c));
  process.stdin.on("end", () => {
    const j = JSON.parse(s);
    if (!j.ok) {
      console.error("chat.postMessage failed:", s);
      process.exit(1);
    }
  });
'
EOF
chmod +x "${EVAL_DIR}/cases/case-001/post-to-crabline.sh"

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001"
ls -la "${EVAL_DIR}/eval.yaml" \
  "${EVAL_DIR}/cases/case-001/"{input,annotations}.yaml \
  "${EVAL_DIR}/cases/case-001/post-to-crabline.sh"
