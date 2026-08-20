#!/usr/bin/env bash
# Host prerequisite: smolclaw Gmail (:8001) + Calendar (:8002) mocks for OpenClaw evals.
# Installs from GitHub (PyPI "smolclaw" is a different project).
#
# Usage:
#   ./examples/start-smolclaw.sh
#   # sandbox must use host.openshell.internal:8001 / :8002 (not 127.0.0.1)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${ROOT}/.tmp/smolclaw"
PY="${ROOT}/.eval-venv/bin/python"
PIP="${ROOT}/.eval-venv/bin/pip"
GMAIL_DB="${PREFIX}/gmail/gmail.db"
GCAL_DB="${PREFIX}/gcal/gcal.db"
GMAIL_PORT="${SMOLCLAW_GMAIL_PORT:-8001}"
GCAL_PORT="${SMOLCLAW_GCAL_PORT:-8002}"
HOST="${SMOLCLAW_HOST:-0.0.0.0}"
REPO_URL="${SMOLCLAW_GIT_URL:-https://github.com/bingran-you/smolclaw.git}"

if [[ ! -x "${PY}" ]]; then
  echo "error: missing ${PY} — create .eval-venv first" >&2
  exit 1
fi

mkdir -p "${PREFIX}/gmail" "${PREFIX}/gcal"

if ! "${PY}" -c "import claw_gmail, claw_gcal" >/dev/null 2>&1; then
  echo "Installing smolclaws from ${REPO_URL} into .eval-venv…"
  "${PIP}" install -q "git+${REPO_URL}"
fi

SMOLCLAW="${ROOT}/.eval-venv/bin/smolclaw"
SMOLCLAW_GCAL="${ROOT}/.eval-venv/bin/smolclaw-gcal"
if [[ ! -x "${SMOLCLAW}" || ! -x "${SMOLCLAW_GCAL}" ]]; then
  echo "error: smolclaw / smolclaw-gcal CLIs missing after install" >&2
  exit 1
fi

_stop_port() {
  local port="$1"
  if lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Killing existing listener on :${port}"
    kill -9 $(lsof -tiTCP:"${port}" -sTCP:LISTEN) 2>/dev/null || true
    sleep 1
  fi
}

_stop_port "${GMAIL_PORT}"
_stop_port "${GCAL_PORT}"

echo "Seeding Gmail (default) → ${GMAIL_DB}"
"${SMOLCLAW}" --db "${GMAIL_DB}" seed --scenario default >/dev/null
echo "Seeding Calendar (default) → ${GCAL_DB}"
"${SMOLCLAW_GCAL}" --db "${GCAL_DB}" seed --scenario default >/dev/null

echo "Starting Mock Gmail on ${HOST}:${GMAIL_PORT}…"
nohup "${SMOLCLAW}" --db "${GMAIL_DB}" serve --host "${HOST}" --port "${GMAIL_PORT}" --no-mcp \
  >"${PREFIX}/gmail/serve.out.log" 2>"${PREFIX}/gmail/serve.err.log" &
echo $! >"${PREFIX}/gmail/serve.pid"

echo "Starting Mock Calendar on ${HOST}:${GCAL_PORT}…"
nohup "${SMOLCLAW_GCAL}" --db "${GCAL_DB}" serve --host "${HOST}" --port "${GCAL_PORT}" --no-mcp \
  >"${PREFIX}/gcal/serve.out.log" 2>"${PREFIX}/gcal/serve.err.log" &
echo $! >"${PREFIX}/gcal/serve.pid"

ready=0
for _ in $(seq 1 40); do
  if curl -sf -m 1 "http://127.0.0.1:${GMAIL_PORT}/gmail/v1/users/me/profile" >/dev/null \
    && curl -sf -m 1 "http://127.0.0.1:${GCAL_PORT}/calendar/v3/users/me/calendarList" >/dev/null; then
    ready=1
    break
  fi
  sleep 0.25
done

if [[ "${ready}" -ne 1 ]]; then
  echo "error: smolclaw not ready; see ${PREFIX}/gmail/serve.err.log and ${PREFIX}/gcal/serve.err.log" >&2
  exit 1
fi

cat >"${PREFIX}/ready.json" <<EOF
{
  "gmail_port": ${GMAIL_PORT},
  "gcal_port": ${GCAL_PORT},
  "gmail_api_host": "http://127.0.0.1:${GMAIL_PORT}/gmail/v1/",
  "gcal_api_host": "http://127.0.0.1:${GCAL_PORT}/calendar/v3/",
  "gmail_api_sandbox": "http://host.openshell.internal:${GMAIL_PORT}/gmail/v1/",
  "gcal_api_sandbox": "http://host.openshell.internal:${GCAL_PORT}/calendar/v3/"
}
EOF

echo "smolclaw ready"
echo "  ready file:   ${PREFIX}/ready.json"
echo "  Gmail (host): http://127.0.0.1:${GMAIL_PORT}/gmail/v1/  (docs …/docs)"
echo "  Gmail (sbx):  http://host.openshell.internal:${GMAIL_PORT}/gmail/v1/"
echo "  Cal (host):   http://127.0.0.1:${GCAL_PORT}/calendar/v3/"
echo "  Cal (sbx):    http://host.openshell.internal:${GCAL_PORT}/calendar/v3/"
echo
echo "Note: Vertex proxy stays on :8000; do not collide with ${GMAIL_PORT}/${GCAL_PORT}."
