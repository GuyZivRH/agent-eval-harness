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
