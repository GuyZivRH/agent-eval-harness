#!/usr/bin/env bash
# Mint / install a read-scoped Slack USER token (xoxp-...) for Forge Slack Read Proxy.
#
# Auth model (forge-outlook-slack + rust-slack-proxy):
#   - SAW onboarding redirect (registered on the Slack app for deployed Forge):
#       {FORGE_ENTRY_ORIGIN}/oauth/callback   → remote Forge entry, not localhost
#   - rust-slack-proxy manifests register NO redirect_uris; local path is
#       Install to Workspace → paste User OAuth Token
#   - Local http://127.0.0.1:8765/callback is NOT registered by default — using it
#       yields: redirect_uri did not match any configured URIs
#
# Prefer Install-to-Workspace for agent-eval-harness. Browser OAuth to localhost
# is opt-in only after you add that exact Redirect URL on the Slack READ app.
#
# Usage:
#   ./examples/forge-real/oauth_slack_read.sh              # print install path (default)
#   ./examples/forge-real/oauth_slack_read.sh --install     # same
#   ./examples/forge-real/oauth_slack_read.sh --localhost   # browser OAuth (requires URI on app)
#   ./examples/forge-real/oauth_slack_read.sh --url-only    # print authorize URL for REDIRECT_URI
#   CODE=… ./examples/forge-real/oauth_slack_read.sh --exchange
#
# After install, put the token in .tmp/forge-real/env:
#   export SLACK_READ_USER_TOKEN='xoxp-...'
#   export SLACK_BOT_TOKEN="$SLACK_READ_USER_TOKEN"
#
# See examples/forge-real/slack-auth.md for SAW vs local planes (incl. slack-drafts-bearer).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${FORGE_REAL_ENV:-${ROOT}/.tmp/forge-real/env}"
OUT_DIR="${ROOT}/.tmp/forge-real"
PORT="${FORGE_SLACK_OAUTH_PORT:-8765}"
# SAW / onboarding exact pattern (remote). Local helper must NOT default to this —
# we cannot receive the callback without a live Forge entry.
SAW_REDIRECT_HINT='{FORGE_ENTRY_ORIGIN}/oauth/callback'
LOCAL_REDIRECT="http://127.0.0.1:${PORT}/callback"
REDIRECT_URI="${FORGE_SLACK_OAUTH_REDIRECT:-${LOCAL_REDIRECT}}"
WAIT_SECS="${FORGE_SLACK_OAUTH_WAIT_SECS:-180}"
USER_SCOPES='channels:history,groups:history,channels:read,groups:read,users:read,users:read.email,search:read'

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi
: "${FORGE_SLACK_READ_CLIENT_ID:?missing FORGE_SLACK_READ_CLIENT_ID in ${ENV_FILE}}"
: "${FORGE_SLACK_TEAM_ID:?missing FORGE_SLACK_TEAM_ID in ${ENV_FILE}}"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true

print_install() {
  cat <<EOF
=== Forge Slack Read — get a usable xoxp token (recommended) ===

Localhost OAuth is broken until you register a Redirect URL. Do this instead:

  1. Open https://api.slack.com/apps
  2. Select app "Forge Slack Read Proxy"
     (client_id=${FORGE_SLACK_READ_CLIENT_ID}, team=${FORGE_SLACK_TEAM_ID})
  3. OAuth & Permissions → Install to Workspace
  4. Copy the User OAuth Token (xoxp-...) into ${ENV_FILE}:

       export SLACK_READ_USER_TOKEN='xoxp-...'
       export SLACK_BOT_TOKEN="\$SLACK_READ_USER_TOKEN"

  5. Verify: ./examples/forge-real/inventory_slack.sh

Notes:
  - SAW registered redirect (deployed Forge only): ${SAW_REDIRECT_HINT}
    → https://<FORGE_ENTRY_HOST>/oauth/callback  (front-proxy /connect flow)
  - That remote callback cannot feed this local helper.
  - Optional local browser OAuth: add exactly this Redirect URL on the READ app,
    then re-run with --localhost:
       ${LOCAL_REDIRECT}
  - /sandbox/persist/.forge-drafts/slack-drafts-bearer is NOT a Slack token
    (drafts↔writer bearer only). See examples/forge-real/slack-auth.md

EOF
}

REDIRECT_URI_ENC="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "${REDIRECT_URI}")"
AUTH_URL="https://slack.com/oauth/v2/authorize?client_id=${FORGE_SLACK_READ_CLIENT_ID}&user_scope=${USER_SCOPES}&team=${FORGE_SLACK_TEAM_ID}&redirect_uri=${REDIRECT_URI_ENC}"

print_url() {
  echo "Open this URL while logged into the ibm-forge-demo / IBM Test workspace:"
  echo
  echo "${AUTH_URL}"
  echo
  echo "Redirect URI (must match Slack READ app Redirect URLs exactly): ${REDIRECT_URI}"
  echo "Expected team_id: ${FORGE_SLACK_TEAM_ID}"
  if [[ "${REDIRECT_URI}" == "${LOCAL_REDIRECT}" ]]; then
    echo
    echo "WARNING: ${LOCAL_REDIRECT} is not registered on the Forge Slack Read app by default."
    echo "If Slack says redirect_uri mismatch, use Install to Workspace (./oauth_slack_read.sh --install)"
    echo "or add that exact URI under OAuth & Permissions → Redirect URLs."
  fi
}

exchange_code() {
  local code="$1"
  : "${FORGE_SLACK_READ_CLIENT_SECRET:?missing FORGE_SLACK_READ_CLIENT_SECRET in ${ENV_FILE} (needed for --exchange/--localhost)}"
  local tmp
  tmp="$(mktemp)"
  # Do not log client_secret or access_token.
  curl -fsS --noproxy '*' -m 30 -X POST 'https://slack.com/api/oauth.v2.access' \
    -d "client_id=${FORGE_SLACK_READ_CLIENT_ID}" \
    -d "client_secret=${FORGE_SLACK_READ_CLIENT_SECRET}" \
    -d "code=${code}" \
    -d "redirect_uri=${REDIRECT_URI}" \
    >"${tmp}" || {
      echo "oauth.v2.access request failed" >&2
      rm -f "${tmp}"
      return 1
    }
  python3 - "${tmp}" "${OUT_DIR}" "${ENV_FILE}" <<'PY'
import json, sys, re
from pathlib import Path
raw = Path(sys.argv[1]).read_text()
out_dir = Path(sys.argv[2])
env_file = Path(sys.argv[3])
data = json.loads(raw)
# Persist response with tokens redacted for debugging
safe = json.loads(raw)
def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in ("token", "secret")) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj
(out_dir / "slack-oauth-read.json").write_text(json.dumps(redact(data), indent=2) + "\n")
if not data.get("ok"):
    err = data.get("error")
    print(f"oauth.v2.access failed: error={err} needed={data.get('needed')}", file=sys.stderr)
    if err == "bad_redirect_uri":
        print(
            "redirect_uri mismatch on token exchange — use Install to Workspace "
            "or register the exact REDIRECT_URI on the Slack READ app.",
            file=sys.stderr,
        )
    sys.exit(2)
authed_team = (data.get("team") or {}).get("id")
authed_name = (data.get("team") or {}).get("name")
# User-token-only apps put the token under authed_user.access_token
token = (data.get("authed_user") or {}).get("access_token") or data.get("access_token")
if not token or not str(token).startswith("xox"):
    print("oauth.v2.access ok but no xox* user token in response", file=sys.stderr)
    sys.exit(3)
# Never print the token
print(f"ok team={authed_name!r} team_id={authed_team} token_prefix={token[:5]}… len={len(token)}")
# Upsert into env file
text = env_file.read_text() if env_file.exists() else ""
for key in ("SLACK_READ_USER_TOKEN", "SLACK_BOT_TOKEN"):
    text = re.sub(rf"^export {key}=.*\n?", "", text, flags=re.M)
block = (
    f"export SLACK_READ_USER_TOKEN={token!r}\n"
    f"export SLACK_BOT_TOKEN={token!r}  # alias for inventory_slack.sh / OpenClaw\n"
)
env_file.write_text(text.rstrip() + "\n" + block)
print(f"wrote SLACK_READ_USER_TOKEN + SLACK_BOT_TOKEN into {env_file} (gitignored)")
PY
  rm -f "${tmp}"
}

run_localhost_wait() {
  print_url
  echo
  echo "Waiting up to ${WAIT_SECS}s for ${REDIRECT_URI} … (Ctrl-C to abort)"

  CODE_FILE="$(mktemp)"
  rm -f "${CODE_FILE}"  # mktemp creates empty file; python waits on Path.exists()
  python3 - "${PORT}" "${CODE_FILE}" "${WAIT_SECS}" <<'PY' &
import sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

port = int(sys.argv[1])
code_file = Path(sys.argv[2])
deadline = time.time() + int(sys.argv[3])

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return  # keep code out of logs
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = (qs.get("code") or [None])[0]
        err = (qs.get("error") or [None])[0]
        body = b"Slack OAuth received. You can close this tab."
        if err:
            code_file.write_text(f"ERROR:{err}")
            body = f"OAuth error: {err}".encode()
        elif code:
            code_file.write_text(code)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

httpd = HTTPServer(("127.0.0.1", port), H)
httpd.timeout = 1.0
while time.time() < deadline and not (code_file.exists() and code_file.stat().st_size > 0):
    httpd.handle_request()
httpd.server_close()
PY
  SERVER_PID=$!

  cleanup() { kill "${SERVER_PID}" 2>/dev/null || true; rm -f "${CODE_FILE}"; }
  trap cleanup EXIT

  for ((i=0; i<WAIT_SECS; i++)); do
    if [[ -s "${CODE_FILE}" ]]; then
      break
    fi
    sleep 1
  done

  if [[ ! -s "${CODE_FILE}" ]]; then
    echo "Timed out waiting for browser OAuth (no code)." >&2
    echo "Next steps:" >&2
    echo "  1) Prefer Install to Workspace: $0 --install" >&2
    echo "  2) Or add Redirect URL ${REDIRECT_URI} on the Slack READ app, then re-run --localhost." >&2
    exit 1
  fi

  GOT="$(cat "${CODE_FILE}")"
  if [[ "${GOT}" == ERROR:* ]]; then
    echo "OAuth denied: ${GOT#ERROR:}" >&2
    exit 1
  fi
  exchange_code "${GOT}"
  echo "Done. Run: ./examples/forge-real/inventory_slack.sh"
}

MODE="${1:-}"
case "${MODE}" in
  ""|--install|--help|-h)
    print_install
    exit 0
    ;;
  --url-only)
    print_url
    exit 0
    ;;
  --exchange)
    : "${CODE:?Set CODE to the oauth ?code= value}"
    exchange_code "${CODE}"
    exit 0
    ;;
  --localhost|--wait|--browser)
    # Opt-in: only works if LOCAL_REDIRECT (or FORGE_SLACK_OAUTH_REDIRECT) is on the app.
    run_localhost_wait
    exit 0
    ;;
  *)
    echo "Unknown arg: ${MODE}" >&2
    echo "Usage: $0 [--install|--localhost|--url-only|--exchange]" >&2
    exit 64
    ;;
esac
