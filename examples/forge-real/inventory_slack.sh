#!/usr/bin/env bash
# Inventory IBM Forge demo Slack channels via Web API (HTTP read path).
# Requires a read-scoped USER token (xoxp-...) from the Forge Slack READ app
# (preferred: SLACK_READ_USER_TOKEN) or SLACK_BOT_TOKEN alias. Bot tokens
# (xoxb) also work if they have channels:read + channels:history.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${FORGE_REAL_ENV:-${ROOT}/.tmp/forge-real/env}"
OUT_DIR="${ROOT}/.tmp/forge-real"
mkdir -p "${OUT_DIR}"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi
# Prefer the Forge read user token; fall back to SLACK_BOT_TOKEN for older setups.
SLACK_TOKEN="${SLACK_READ_USER_TOKEN:-${SLACK_BOT_TOKEN:-}}"
: "${SLACK_TOKEN:?Set SLACK_READ_USER_TOKEN (xoxp-...) via Install to Workspace — see ./oauth_slack_read.sh --install}"
export SLACK_BOT_TOKEN="${SLACK_TOKEN}"  # keep python block / history curl working
SLACK_API_URL="${SLACK_API_URL:-https://slack.com/api/}"
CHANNELS="${FORGE_SLACK_CHANNELS:-ibm-watson-orchestrate-news,ibm-quantum-news,ibm-exec-product-views-n-strategy}"
export CHANNELS
export SLACK_API_URL

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true

auth_hdr=(-H "Authorization: Bearer ${SLACK_TOKEN}")

echo "auth.test…"
curl -fsS --noproxy '*' -m 30 "${auth_hdr[@]}" \
  "${SLACK_API_URL}auth.test" | tee "${OUT_DIR}/slack-auth.json" >/dev/null
python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path(".tmp/forge-real/slack-auth.json").read_text())
assert d.get("ok"), d
print(f"ok team={d.get('team')} user={d.get('user')} bot_id={d.get('bot_id')}")
PY

echo "conversations.list…"
curl -fsS --noproxy '*' -m 60 "${auth_hdr[@]}" \
  "${SLACK_API_URL}conversations.list?types=public_channel,private_channel&limit=200" \
  | tee "${OUT_DIR}/slack-channels.json" >/dev/null

python3 - <<PY
import json, os
from pathlib import Path
wanted = [c.strip().lstrip("#") for c in os.environ["CHANNELS"].split(",") if c.strip()]
data = json.loads(Path(".tmp/forge-real/slack-channels.json").read_text())
assert data.get("ok"), data
by_name = {c["name"]: c for c in data.get("channels") or []}
inventory = {"channels": [], "missing": []}
for name in wanted:
    ch = by_name.get(name)
    if not ch:
        inventory["missing"].append(name)
        print(f"MISSING #{name}")
        continue
    cid = ch["id"]
    print(f"FOUND #{name} id={cid} is_member={ch.get('is_member')}")
    hist = __import__("urllib.request").request.urlopen(
        __import__("urllib.request").request.Request(
            f"{os.environ['SLACK_API_URL']}conversations.history?channel={cid}&limit=30",
            headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
        ),
        timeout=60,
    ).read()
    h = json.loads(hist)
    path = Path(f".tmp/forge-real/slack-history-{name}.json")
    path.write_text(json.dumps(h, indent=2))
    msgs = h.get("messages") or []
    themes = []
    for m in msgs[:15]:
        text = (m.get("text") or "").replace("\n", " ")
        themes.append({"ts": m.get("ts"), "user": m.get("user"), "text": text[:240]})
        print(f"  - {text[:120]}")
    inventory["channels"].append({
        "name": name,
        "id": cid,
        "is_member": ch.get("is_member"),
        "message_count_sample": len(msgs),
        "sample": themes,
    })
Path(".tmp/forge-real/slack-inventory.json").write_text(json.dumps(inventory, indent=2))
print(f"wrote .tmp/forge-real/slack-inventory.json missing={inventory['missing']}")
if inventory["missing"]:
    raise SystemExit(2)
PY
