#!/usr/bin/env bash
# Start / resume Microsoft device-code login for tbx-demo3 Graph access.
# Writes .tmp/forge-real/device-code.json then polls into m365-token.json
# and appends M365_ACCESS_TOKEN to .tmp/forge-real/env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${ROOT}/.tmp/forge-real"
ENV_FILE="${OUT}/env"
PY="${ROOT}/.eval-venv/bin/python"
mkdir -p "${OUT}"
# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy || true

"${PY}" - <<'PY'
import json, os, time, urllib.parse, urllib.request
from pathlib import Path
out = Path(".tmp/forge-real")
tid = os.environ["M365_TENANT_ID"]
cid = os.environ["M365_CLIENT_ID"]
sec = os.environ["M365_CLIENT_SECRET"]
scope = (
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/Mail.Send "
    "https://graph.microsoft.com/Calendars.ReadWrite "
    "https://graph.microsoft.com/User.Read offline_access"
)
data = urllib.parse.urlencode({"client_id": cid, "scope": scope}).encode()
req = urllib.request.Request(
    f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/devicecode",
    data=data,
    method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    dc = json.load(r)
(out / "device-code.json").write_text(json.dumps(dc, indent=2))
print(dc.get("message"))
print(f"user_code={dc.get('user_code')} expires_in={dc.get('expires_in')}")
for i in range(120):
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": cid,
            "client_secret": sec,
            "device_code": dc["device_code"],
        }
    ).encode()
    treq = urllib.request.Request(
        f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(treq, timeout=30) as r:
            tok = json.load(r)
        (out / "m365-token.json").write_text(
            json.dumps(
                {
                    "access_token": tok["access_token"],
                    "refresh_token": tok.get("refresh_token"),
                    "expires_in": tok.get("expires_in"),
                    "scope": tok.get("scope"),
                },
                indent=2,
            )
        )
        env_path = out / "env"
        lines = env_path.read_text().splitlines() if env_path.exists() else []
        lines = [ln for ln in lines if not ln.startswith("export M365_ACCESS_TOKEN=")]
        lines.append(f"export M365_ACCESS_TOKEN={tok['access_token']!r}")
        env_path.write_text("\n".join(lines) + "\n")
        print("TOKEN_OK scopes=", tok.get("scope"))
        break
    except Exception as e:
        raw = e.read().decode() if hasattr(e, "read") else str(e)
        try:
            err = json.loads(raw)
        except Exception:
            err = {"error": "http", "error_description": raw[:200]}
        print(i, err.get("error"), (err.get("error_description") or "")[:100])
        if err.get("error") not in ("authorization_pending", "slow_down"):
            raise SystemExit(1)
        time.sleep(int(dc.get("interval") or 5))
else:
    raise SystemExit("device-code timed out")
PY
