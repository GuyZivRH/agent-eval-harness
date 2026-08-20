# AEH + OpenShell + OpenClaw (Quay) — end-to-end guide

This guide is for someone starting from a clean machine. It explains the stack we
validated, how to start the host LLM proxy, how to run the three demo cases, how
to keep sandboxes alive for inspection, and how to prove the run really used
**Agent Eval Harness (AEH) → OpenShell → Quay OpenClaw** — not Claude Code, not
Harbor, not a host-local OpenClaw process.

## What this stack is

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────────────┐
│ AEH (host)   │────▶│ OpenShell       │────▶│ Quay OpenClaw sandbox    │
│ openshell.run│     │ gateway +       │     │ openclaw agent exec      │
│ judges/report│     │ sandbox create  │     │ image: quay.io/.../openclaw
└──────────────┘     └────────┬────────┘     └────────────┬─────────────┘
                              │                           │
                              │  inference.local (HTTPS)  │
                              ▼                           │
                     ┌─────────────────┐                  │
                     │ Host FastAPI    │◀── host.openshell.internal:8000
                     │ Vertex proxy    │
                     │ :8000 (HTTP)    │
                     └────────┬────────┘
                              ▼
                         Google Vertex
```

| Layer | Role |
|-------|------|
| **AEH** | Stages cases, creates sandboxes, runs `openclaw agent exec`, harvests trajectory → `events.json`, scores judges, writes `report.html` |
| **OpenShell** | Policy-enforced sandboxes (`openshell sandbox create/exec`), routes model traffic via the `inference` provider |
| **OpenClaw (Quay)** | Agent binary inside the sandbox (`OpenClaw 2026.7.2-beta.7`); talks to `https://inference.local/v1` |
| **Host proxy** | OpenAI-compatible HTTP API on `:8000` backed by Vertex Claude |

Validated image:

```text
quay.io/aipcc/base-images/agentic/openclaw:latest
→ OpenClaw 2026.7.2-beta.7 (dabe191)
→ OpenClaw packaged under /opt/openclaw
```

Landlock policy used by AEH: `deploy/openshell/eval-policy.yaml`
(includes `/opt/openclaw` so the Quay layout is readable).

---

## Prerequisites

1. **This repo** checked out (paths below assume `/Users/gziv/Dev/agent-eval-harness` — adjust).
2. **Python 3.11+** and a fresh AEH `.eval-venv` (install list below).
3. **Podman** (OpenShell local compute driver on Mac).
4. **OpenShell CLI + local gateway** (install section below).
5. **Google Cloud ADC** for Vertex (`gcloud auth application-default login`).
6. Network access to pull the Quay image (once).

### Install OpenShell on macOS (Homebrew)

Official path: NVIDIA’s install script uses Homebrew under the hood (CLI +
gateway binary + `brew services` unit).

```bash
# 1) Podman first (compute driver) — machine must be running before the gateway
brew install podman
podman machine init   # once
podman machine start

# 2) Install OpenShell (downloads formula into a Homebrew tap, installs, starts gateway)
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh

# 3) Confirm CLI + service
openshell --version
brew services info openshell
# restart later if needed:
#   brew services restart openshell
```

If the formula is already tapped on your machine, this is equivalent:

```bash
brew install openshell
brew services restart openshell
```

Uninstall later:

```bash
brew services stop openshell
brew uninstall openshell
```

#### macOS: configure the Podman compute driver

Fresh OpenShell (0.0.109+) does **not** always auto-detect Podman. If
`brew services info openshell` shows **Loaded** but not healthy, or the err log
says `no compute driver configured` / `no responsive Podman API socket`, write
`/opt/homebrew/var/openshell/gateway.env` and restart:

```bash
podman machine start
SOCKET=$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
echo "SOCKET=$SOCKET"
curl -sS -m 3 --unix-socket "$SOCKET" http://d/_ping
echo

python3 - "$SOCKET" <<'PY'
import os, sys
from pathlib import Path
socket = sys.argv[1]
path = Path("/opt/homebrew/var/openshell/gateway.env")
vals = {}
if path.exists():
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v
vals["OPENSHELL_DRIVERS"] = "podman"
vals["OPENSHELL_PODMAN_SOCKET"] = socket
vals.setdefault("OPENSHELL_SSH_HANDSHAKE_SECRET", os.urandom(32).hex())
path.write_text(
    "OPENSHELL_DRIVERS={OPENSHELL_DRIVERS}\n"
    "OPENSHELL_SSH_HANDSHAKE_SECRET={OPENSHELL_SSH_HANDSHAKE_SECRET}\n"
    "OPENSHELL_PODMAN_SOCKET={OPENSHELL_PODMAN_SOCKET}\n".format(**vals)
)
path.chmod(0o600)
print("wrote", path)
print("OPENSHELL_PODMAN_SOCKET=" + vals["OPENSHELL_PODMAN_SOCKET"])
PY

brew services restart openshell
sleep 3
unset OPENSHELL_GATEWAY_ENDPOINT
openshell status
openshell sandbox list
```

Expect curl to print `OK`, `openshell status` to show **Connected** + **Authenticated (mTLS)**, and
`sandbox list` to print an empty table (not a gRPC 404).

Note: interactive zsh does **not** treat `# …` as comments unless you
`setopt interactivecomments`. Do not paste comment-only lines into the shell.

Notes:

- The Podman API socket path can live under a project `.tmp/podman/…` directory
  (Podman Machine chooses it when `podman-mac-helper` is not installed). After
  `podman machine stop/start`, re-check the path and update `gateway.env` if it
  changed, then `brew services restart openshell`.
- Do not leave an old manual/`screen` `openshell-gateway` running alongside
  `brew services` — both fight for `:17670`.

Gateway config (Homebrew) usually lives at
`/opt/homebrew/var/openshell/gateway.toml`. Current packages typically enable
**TLS + mTLS** and register the CLI gateway as `https://localhost:17670`.

### Gateway endpoint for CLI + AEH

**Do not force `http://[::1]:17670` on a TLS brew install.** That env override
talks plaintext HTTP to an HTTPS listener and fails with
`HTTP 404` / `grpc-status header missing` even when the gateway is healthy.

Preferred (CLI): use the registered gateway, no endpoint override:

```bash
unset OPENSHELL_GATEWAY_ENDPOINT
openshell gateway list
openshell status
openshell sandbox list
```

For AEH (and any script that needs an explicit URL), export the **same**
endpoint `openshell gateway list` shows — on a fresh brew install that is usually:

```bash
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
```

| Value | When to use |
|-------|-------------|
| *(unset)* | CLI uses the active registered gateway (best for interactive `openshell …`) |
| `https://localhost:17670` | Typical brew 0.0.109+ installer registration (mTLS) — use this for AEH |
| `https://127.0.0.1:17670` | AEH Python default if env unset; works when the gateway also listens on IPv4 |
| `http://[::1]:17670` | Only if **your** gateway is actually plaintext HTTP on IPv6 loopback |

`[::1]` means IPv6 localhost; brackets are required in URLs. Scheme must match
how the gateway listens.

```bash
openshell gateway list
grep -n bind_address /opt/homebrew/var/openshell/gateway.toml 2>/dev/null
```

### Fresh `.eval-venv` install list (this e2e)

AEH’s **core** package (`pyproject.toml`) only always needs `pyyaml`, `jinja2`,
and `truststore`. `anthropic[vertex]` is an **optional** extra used for LLM
judges (and for the host Vertex proxy deps below).

`scripts/ensure_deps.py` / `/eval-setup` can install `anthropic[vertex]` when
they detect certain judge shapes in `eval.yaml`. This demo’s judge uses
`llm_rubric`, which **ensure_deps does not currently treat as a trigger** — so
for a clean venv, install the extras explicitly:

```bash
cd /Users/gziv/Dev/agent-eval-harness

python3 -m venv .eval-venv
.eval-venv/bin/pip install -U pip

# AEH package + extras needed for this OpenShell e2e (scoring + LLM judges)
.eval-venv/bin/pip install -e '.[anthropic]'

# Equivalent explicit pins (same as pyproject optional-deps):
#   .eval-venv/bin/pip install -e .
#   .eval-venv/bin/pip install 'anthropic[vertex]>=0.40' 'jinja2>=3.1' 'pyyaml>=6.0' 'truststore>=0.9,<1.0'

# Sanity
.eval-venv/bin/python -c "import agent_eval, yaml, jinja2, anthropic; print('aeh venv ok', anthropic.__version__)"
```

Optional (not required for this e2e):

| Extra | Install | When |
|-------|---------|------|
| MLflow | `pip install -e '.[mlflow]'` | `/eval-mlflow` / tracking |
| ANOVA | `pip install -e '.[anova]'` | `/eval-anova` |
| Harbor | `pip install -e '.[harbor]'` (Python ≥3.12) | Harbor runner |
| Everything | `pip install -e '.[all]'` | kitchen sink |

**Host proxy** (same `.eval-venv` is fine — do **not** use system `python3 -m pip`
on Homebrew Python; that hits PEP 668 `externally-managed-environment`):

```bash
.eval-venv/bin/pip install fastapi uvicorn
# anthropic[vertex] already installed via -e '.[anthropic]' above
```

---

## Part 1 — Start the host Claude/Vertex proxy

Use a **dedicated terminal**. Leave this process running for the whole eval.

### 1.1 Install proxy deps (once)

Prefer the AEH venv (already has `anthropic[vertex]` if you followed Prerequisites):

```bash
cd /Users/gziv/Dev/agent-eval-harness
.eval-venv/bin/pip install fastapi uvicorn
```

If `.eval-venv` does not exist yet, create it with the **Fresh `.eval-venv` install list** above, then install `fastapi` / `uvicorn`.

### 1.2 Authenticate to Google Cloud (once)

Check whether Application Default Credentials (ADC) already exist:

```bash
test -f "$HOME/.config/gcloud/application_default_credentials.json" && echo "ADC file present" || echo "ADC file missing"
gcloud auth application-default print-access-token >/dev/null && echo "ADC token OK" || echo "ADC token failed — login needed"
gcloud auth list
```

If the token check fails (or the ADC file is missing), log in once:

```bash
gcloud auth application-default login
```

Default ADC path: `~/.config/gcloud/application_default_credentials.json`.

### 1.3 Start the proxy

The script lives in the repo: `examples/claude-vertex-proxy.py`.

```bash
# free :8000 if something stale is listening
kill -9 $(lsof -tiTCP:8000 -sTCP:LISTEN) 2>/dev/null

# avoid corporate proxies breaking Vertex / localhost
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
export NO_PROXY='*' no_proxy='*'

export ANTHROPIC_VERTEX_PROJECT_ID="${ANTHROPIC_VERTEX_PROJECT_ID:-itpc-gcp-eco-eng-claude}"
export CLOUD_ML_REGION="${CLOUD_ML_REGION:-us-east5}"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-$HOME/.config/gcloud/application_default_credentials.json}"

cd /Users/gziv/Dev/agent-eval-harness
.eval-venv/bin/python examples/claude-vertex-proxy.py
```

You should see: `Starting Claude Vertex proxy on http://0.0.0.0:8000`.

> Bind is **HTTP** on `0.0.0.0:8000`. OpenShell’s provider will reach it as
> `http://host.openshell.internal:8000/v1`. Inside the sandbox, OpenClaw uses
> **HTTPS** `https://inference.local/v1` (OpenShell terminates TLS).

---

## Part 2 — Verify the proxy (second terminal)

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok"}

curl -s http://127.0.0.1:8000/v1/models
# lists claude-sonnet-4 / claude

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4","messages":[{"role":"user","content":"Reply with exactly: pong"}],"max_tokens":16}'
# choices[0].message.content should contain "pong"
```

If health works but completions fail, fix Vertex auth / project / region before
continuing.

---

## Part 3 — Wire OpenShell → host proxy (once per machine)

Still in the second terminal (gateway must be up):

```bash
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'

# Point the named provider at the host proxy (HTTP)
openshell provider update inference \
  --config OPENAI_BASE_URL=http://host.openshell.internal:8000/v1 \
  --credential OPENAI_API_KEY=empty

# Route sandbox inference.local to that provider
openshell inference set --provider inference --model claude-sonnet-4 --no-verify

openshell provider list
openshell inference get
```

If `provider update` fails because `inference` does not exist yet, create it
first (OpenShell version-dependent); then run `update` + `inference set` again.

Quick in-sandbox smoke (optional):

```bash
openshell sandbox create --name proxy-smoke \
  --from quay.io/aipcc/base-images/agentic/openclaw:latest \
  --policy /Users/gziv/Dev/agent-eval-harness/deploy/openshell/eval-policy.yaml \
  --provider inference \
  --no-tty --no-auto-providers -- echo ready

openshell sandbox exec -n proxy-smoke --workdir /sandbox -- \
  sh -c 'curl -sk https://inference.local/v1/models | head -c 200; echo'

openshell sandbox delete proxy-smoke
```

---

## Part 4 — The three demo use cases

Eval root: `eval/openclaw-openshell/`

### 4.0 Bootstrap the eval package (required on a fresh machine)

This demo tree may not exist on another checkout until you create it (or until
it is committed upstream). From the repo root:

```bash
cd /Users/gziv/Dev/agent-eval-harness
chmod +x examples/bootstrap-openclaw-openshell-eval.sh
./examples/bootstrap-openclaw-openshell-eval.sh
```

That writes:

```text
eval/openclaw-openshell/eval.yaml
eval/openclaw-openshell/cases/case-001/{input.yaml,annotations.yaml}
eval/openclaw-openshell/cases/case-002/{input.yaml,annotations.yaml}
eval/openclaw-openshell/cases/case-003/{input.yaml,annotations.yaml}
```

Re-running the script overwrites those demo files (safe). Skip this step only
if that tree already exists and matches the cases below.
| Case | Purpose | Prompt | Expected |
|------|---------|--------|----------|
| **case-001** | Factual one-word answer | `What is the capital of France? Answer in one word.` | `Paris` |
| **case-002** | Simple arithmetic | `What is 15 + 27? Just give the number.` | `42` |
| **case-003** | Common-knowledge color | `What color is the sky on a clear day? One word answer.` | `Blue` |

### case-001

`cases/case-001/input.yaml`:

```yaml
prompt: "What is the capital of France? Answer in one word."
```

`cases/case-001/annotations.yaml`:

```yaml
expected: Paris
```

### case-002

`cases/case-002/input.yaml`:

```yaml
prompt: "What is 15 + 27? Just give the number."
```

`cases/case-002/annotations.yaml`:

```yaml
expected: "42"
```

### case-003

`cases/case-003/input.yaml`:

```yaml
prompt: "What color is the sky on a clear day? One word answer."
```

`cases/case-003/annotations.yaml`:

```yaml
expected: Blue
```

### How they are scored (`eval.yaml`)

- **correct_answer** — expected substring in the agent response (deterministic)
- **llm_correctness** — Vertex LLM rubric on the host (not inside the sandbox)
- **response_received** — non-empty response
- **no_error** — stderr does not look like a hard error

The agent runs as OpenClaw **prompt mode** (`execution.prompt`) with
`runner.type: openclaw` and provider `inference` → `https://inference.local/v1`.

---

## Part 5 — Full e2e run (AEH)

### 5.1 One-time AEH venv (if needed)

Create/install `.eval-venv` using the **Fresh `.eval-venv` install list** in
Prerequisites (above). Then:

```bash
cd /Users/gziv/Dev/agent-eval-harness
.eval-venv/bin/python -c "import agent_eval, anthropic; print('ok')"
```

### 5.2 Run all three cases (normal — sandboxes deleted after each case)

```bash
cd /Users/gziv/Dev/agent-eval-harness

export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
export AGENT_EVAL_OPENSHELL_IMAGE='quay.io/aipcc/base-images/agentic/openclaw:latest'
export AGENT_EVAL_OPENSHELL_POLICY="$PWD/deploy/openshell/eval-policy.yaml"
export AGENT_EVAL_OPENSHELL_PROVIDER=inference
export AGENT_EVAL_RUNS_DIR="$PWD/eval/openclaw-openshell/eval/runs"

RUN_ID="quay-e2e-$(date +%Y%m%d-%H%M%S)"

.eval-venv/bin/python -m agent_eval.openshell.run \
  --config "$PWD/eval/openclaw-openshell/eval.yaml" \
  --model inference/claude-sonnet-4 \
  --run-id "$RUN_ID" \
  2>&1 | tee "$PWD/.tmp/aeh-${RUN_ID}.log"
```

Expected log highlights:

- `Staging workspaces…`
- `Creating sandbox e-<hex>-001` (AEH names sandboxes)
- `Harvested N events from OpenClaw trajectory…`
- `Scoring 3 cases with 4 judges…` including `llm_correctness`
- `REPORT: …/report.html`

Results:

```text
eval/openclaw-openshell/eval/runs/openclaw-openshell-test/<RUN_ID>/
  report.html
  summary.yaml
  cases/case-00N/
    stdout.log          # OpenClaw agent-exec JSON envelope
    events.json         # AEH-normalized trajectory (user/assistant/tools/…)
    openclaw-trajectory-events.jsonl   # raw OpenClaw export (when harvest works)
    run_result.json
    output/response.txt
```

Notes:

- A warning `Failed to download … /sandbox/output` is **expected** for these
  prompt-only cases (no output dir created inside the sandbox). AEH still writes
  `output/response.txt` from the OpenClaw envelope on the host.
- Model flag must be `inference/claude-sonnet-4` (provider prefix + id from
  `eval.yaml`).

### 5.3 Keep sandboxes alive for inspection

Add **`--keep-sandbox`** (or `AGENT_EVAL_OPENSHELL_KEEP_RUN=1`):

```bash
RUN_ID="quay-verify-keep-$(date +%Y%m%d-%H%M%S)"

.eval-venv/bin/python -m agent_eval.openshell.run \
  --config "$PWD/eval/openclaw-openshell/eval.yaml" \
  --model inference/claude-sonnet-4 \
  --run-id "$RUN_ID" \
  --keep-sandbox \
  2>&1 | tee "$PWD/.tmp/aeh-${RUN_ID}.log"
```

Log lines look like:

```text
Kept sandbox e-2b0c3097-001: openshell sandbox connect e-2b0c3097-001
```

**Sandbox names change every run.** Copy them from the log; do not reuse names
from an older guide paste.

List them:

```bash
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
openshell sandbox list
```

---

## Part 6 — Inspect and prove AEH + OpenShell + OpenClaw

Set `SB` to one kept sandbox from your log (example only):

```bash
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
SB=e-REPLACE-WITH-YOUR-SANDBOX   # e.g. e-2b0c3097-001
```

### 6.1 It is OpenShell

```bash
openshell --version
openshell sandbox list | grep "$SB"
# Interactive shell:
openshell sandbox connect "$SB"
```

AEH creates sandboxes via `openshell sandbox create --from <IMAGE> --policy … --provider inference`.

### 6.2 It is Quay OpenClaw (not Claude Code)

```bash
# Version string
openshell sandbox exec -n "$SB" --workdir /sandbox -- openclaw --version
# → OpenClaw 2026.7.2-beta.7 (dabe191)

# Quay image packages OpenClaw under /opt/openclaw
openshell sandbox exec -n "$SB" --workdir /sandbox -- sh -c '
  command -v openclaw
  command -v node; node --version
  node -p "require(\"/opt/openclaw/package.json\").dependencies.openclaw"
  command -v claude || echo NO_claude
  command -v codex || echo NO_codex
'
# Expect: /usr/local/bin/openclaw, Node 22.x+, dependency 2026.7.2-beta.7, NO_claude
```

(`which` is often missing in the image — use `command -v`.)

### 6.3 Inference goes through OpenShell (not direct Anthropic)

```bash
openshell sandbox exec -n "$SB" --workdir /sandbox -- sh -c '
  curl -sk -m 5 https://inference.local/v1/models | head -c 300; echo
'
# Should list claude-sonnet-4 (proxied to host :8000)
```

Host-side AEH run stdout should show:

```json
"provider": "inference",
"model": "claude-sonnet-4"
```

### 6.4 AEH managed the sandbox contents

```bash
openshell sandbox exec -n "$SB" --workdir /sandbox \
  --env HOME=/sandbox \
  --env OPENCLAW_STATE_DIR=/sandbox/.openclaw \
  --env OPENCLAW_CONFIG_PATH=/sandbox/openclaw-eval.json \
  --env TMPDIR=/sandbox/tmp \
  -- sh -c '
    ls -la /sandbox
    echo "--- config (AEH-written) ---"
    head -c 400 /sandbox/openclaw-eval.json; echo
    echo "--- state ---"
    ls /sandbox/.openclaw
    ls /sandbox/.openclaw/trajectory-exports 2>/dev/null
    echo "--- sessions (SQLite-backed) ---"
    openclaw --version
    openclaw sessions --json | head -c 600; echo
  '
```

Expect:

- `/sandbox/openclaw-eval.json` — providers.inference → `https://inference.local/v1`
- `/sandbox/.openclaw/` with `agents/`, `trajectory-exports/aeh-case-00N/`
- Session key shaped like `agent:main:explicit:<uuid>` (from `agent exec`)
- Uploaded case tree under `/sandbox/case-00N/` (input.yaml, etc.)

### 6.5 Host artifacts prove AEH orchestration

```bash
RUN=eval/openclaw-openshell/eval/runs/openclaw-openshell-test/$RUN_ID

rg -n 'Creating sandbox|Kept sandbox|Harvested|Scoring' ".tmp/aeh-${RUN_ID}.log"

ls -la "$RUN/cases/case-001"
# events.json, openclaw-trajectory-events.jsonl, stdout.log, run_result.json, …

python3 - <<PY
import json
from pathlib import Path
run = Path("$RUN")
stdout = json.loads((run / "cases/case-001/stdout.log").read_text())
print("provider=", stdout.get("provider"), "model=", stdout.get("model"))
print("final=", stdout.get("final"))
events = json.loads((run / "cases/case-001/events.json").read_text())
print("event types=", [e["type"] for e in events])
PY
```

Trajectory harvest (Quay / OpenClaw 2026.7.x): AEH keeps `--state-dir`, then runs
`openclaw sessions export-trajectory` and maps `events.jsonl` into AEH
`events.json` (tools/thinking when present; these three demos are short Q&A).

---

## Part 7 — Cleanup

```bash
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'

# delete the sandboxes you kept (use YOUR names from the log)
openshell sandbox delete e-REPLACE-001
openshell sandbox delete e-REPLACE-002
openshell sandbox delete e-REPLACE-003

# stop the proxy terminal with Ctrl-C
# optional: free the port
kill -9 $(lsof -tiTCP:8000 -sTCP:LISTEN) 2>/dev/null
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `no compute driver configured` | Set `OPENSHELL_DRIVERS=podman` in `/opt/homebrew/var/openshell/gateway.env`, restart brew service |
| `no responsive Podman API socket` / Connection refused on `…api.sock` | `podman machine start`, refresh `OPENSHELL_PODMAN_SOCKET` from `podman machine inspect`, restart openshell |
| `Schedulable: false` / gateway crash-loop | Same as above; also kill any old `screen`/manual `openshell-gateway` competing on `:17670` |
| `HTTP 404` / `grpc-status header missing` | Unset plaintext `OPENSHELL_GATEWAY_ENDPOINT=http://…`; use registered HTTPS gateway (`openshell status` should say Connected + mTLS) |
| `Config not found: eval/…` | Use an absolute `--config` path; ensure `eval/openclaw-openshell/` exists |
| Proxy health OK, sandbox `503` / inference unavailable | Re-run Part 3 (`provider update` + `inference set`); confirm proxy still listening |
| `Unknown model` inside OpenClaw | AEH must pass `--config` with providers (not `--auth-env-only` alone); check `/sandbox/openclaw-eval.json` |
| `Permission denied` under `/opt/openclaw` | Set `AGENT_EVAL_OPENSHELL_POLICY` to `deploy/openshell/eval-policy.yaml` |
| Empty `events.json` | Need retained `--state-dir` (AEH does this) + successful `export-trajectory`; check log for harvest warnings |
| LLM judge import / auth errors | `.eval-venv/bin/pip install 'anthropic[vertex]'` and valid ADC |
| `which: command not found` in sandbox | Use `command -v` |
| `openshell sandbox inspect` | Not a real subcommand; use `list` / `connect` / `exec` |

---

## Quick reference — env vars

| Variable | Meaning |
|----------|---------|
| `OPENSHELL_GATEWAY_ENDPOINT` | Gateway URL for AEH/CLI override. Fresh brew: `https://localhost:17670` (mTLS). Do **not** force `http://[::1]:17670` against a TLS gateway. |
| `AGENT_EVAL_OPENSHELL_IMAGE` | Quay OpenClaw image |
| `AGENT_EVAL_OPENSHELL_POLICY` | Landlock policy YAML (must allow `/opt/openclaw`) |
| `AGENT_EVAL_OPENSHELL_PROVIDER` | OpenShell provider name (`inference`) |
| `AGENT_EVAL_RUNS_DIR` | Where AEH writes runs |
| `AGENT_EVAL_OPENSHELL_KEEP_RUN=1` | Same as `--keep-sandbox` |

Related files:

- Bootstrap eval package: `examples/bootstrap-openclaw-openshell-eval.sh`
- Eval: `eval/openclaw-openshell/eval.yaml` + `cases/`
- Policy: `deploy/openshell/eval-policy.yaml`
- Proxy: `examples/claude-vertex-proxy.py`
- Orchestrator: `python -m agent_eval.openshell.run`
