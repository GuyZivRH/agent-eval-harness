# AEH + OpenShell + OpenClaw → Crabline (Slack mock) — agent eval guide

This guide extends the Quay OpenClaw e2e stack so the **agent inside the
sandbox** talks to a **host Crabline Slack mock** via real `exec` + `curl`, and
AEH scores the Crabline recorder (not just the final text).

It assumes the previous guide already works on your machine.

## Prerequisite

Complete and leave healthy:

**[AEH + OpenShell + OpenClaw (Quay) — end-to-end guide](./openshell-openclaw-e2e.md)**

That means you already have:

- Podman + OpenShell gateway (`openshell status` Connected + mTLS)
- `.eval-venv` with `pip install -e '.[anthropic]'` (+ `fastapi` / `uvicorn`)
- Host Vertex proxy on `:8000` wired to OpenShell `inference` → `inference.local`
- Quay image pull + `deploy/openshell/eval-policy.yaml` (includes `/opt/openclaw`)
- A successful prompt-only run under `eval/openclaw-openshell/` (optional but recommended)

This Crabline agent eval **reuses** that stack. It adds host Crabline on `:8787`
and a different eval package (`eval/openclaw-crabline-agent/`).

---

## What this stack is

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────────────┐
│ AEH (host)   │────▶│ OpenShell       │────▶│ Quay OpenClaw sandbox    │
│ openshell.run│     │ gateway +       │     │ openclaw agent exec      │
│ + Crabline   │     │ sandbox create  │     │ exec → curl → Crabline   │
│ seed/judges  │     │                 │     │ image: quay.io/.../openclaw
└──────┬───────┘     └────────┬────────┘     └────────────┬─────────────┘
       │                      │                           │
       │ seed / score         │  inference.local (HTTPS)  │
       ▼                      ▼                           │
┌─────────────────┐  ┌─────────────────┐                  │
│ Host Crabline   │  │ Host FastAPI    │◀── host.openshell.internal:8000
│ Slack mock      │  │ Vertex proxy    │
│ :8787 (HTTP)    │  │ :8000 (HTTP)    │
└────────▲────────┘  └────────┬────────┘
         │                    ▼
         │               Google Vertex
         │
         └── sandbox: host.openshell.internal:8787/api/
```

| Layer | Role |
|-------|------|
| **AEH** | Stages cases, optional **host seed** into Crabline, creates sandboxes, runs `openclaw agent exec`, harvests trajectory → `events.json`, scores Crabline recorder + tool use, writes `report.html` |
| **OpenShell** | Policy-enforced sandboxes; allows egress to `inference.local` **and** `host.openshell.internal:8787` |
| **OpenClaw (Quay)** | Agent uses the **`exec` tool** to `curl` Slack Web API–shaped endpoints on Crabline (not `channels.slack`) |
| **Host Crabline** | Local Slack mock (`serve slack`) + JSONL **recorder** of accepted API calls |
| **Host proxy** | Same Vertex OpenAI-compatible proxy as the e2e guide — **must support tool calling** |

Validated image (unchanged from e2e):

```text
quay.io/aipcc/base-images/agentic/openclaw:latest
→ OpenClaw 2026.7.2-beta.7
```

Landlock policy: `deploy/openshell/eval-policy.yaml` (includes `crabline_slack`
egress for `curl` / `node` → `host.openshell.internal:8787`).

### Out of scope / known fail

OpenClaw **gateway + `channels.slack`** inbound under OpenShell still
**segfaults (exit 139)** on Quay `2026.7.2-beta.7`. This eval deliberately
avoids that path: agent = `exec` + curl only.

---

## Extra installs (after the e2e venv is working)

You do **not** need a new Python venv. Confirm the e2e packages, then add
**Node/npm** for Crabline (host only).

### Python (same `.eval-venv`)

```bash
cd /Users/gziv/Dev/agent-eval-harness

.eval-venv/bin/python -c "import agent_eval, yaml, jinja2, anthropic; print('aeh ok', anthropic.__version__)"
.eval-venv/bin/python -c "import fastapi, uvicorn; print('proxy deps ok')"
```

If either fails, re-run the e2e **Fresh `.eval-venv` install list**, then:

```bash
.eval-venv/bin/pip install -e '.[anthropic]'
.eval-venv/bin/pip install fastapi uvicorn
```

Fresh `.eval-venv` extras for this guide (after the e2e install list):

```bash
# Crabline: Node/npm (start-crabline-slack.sh installs @openclaw/crabline under .tmp/)
# smolclaw Gmail/Calendar mocks — install from GitHub (NOT PyPI "smolclaw"):
.eval-venv/bin/pip install 'git+https://github.com/bingran-you/smolclaw.git'
# Or just run ./examples/start-smolclaw.sh (installs if missing)
```


`examples/start-crabline-slack.sh` installs `@openclaw/crabline` under
`.tmp/crabline/` via npm (once). You need a working `npm` / Node on the host:

```bash
node --version   # e.g. v20+
npm --version
```

If missing on macOS: `brew install node`.

---

## Part 1 — Start the host Crabline Slack mock

Use a **dedicated terminal** (or leave the process in the background — the
start script uses `nohup`). Crabline must stay up for the whole eval.

### 1.1 Start

```bash
cd /Users/gziv/Dev/agent-eval-harness
./examples/start-crabline-slack.sh
```

Expect something like:

```text
Crabline Slack ready
  ready file:     …/.tmp/crabline/ready/slack-server.json
  recorder:       …/.tmp/crabline/recorders/slack.jsonl
  apiRoot (host): http://127.0.0.1:8787/api/
  apiRoot (sbx):  http://host.openshell.internal:8787/api/
  botToken:       xoxb-…
```

| Path | Purpose |
|------|---------|
| `.tmp/crabline/ready/slack-server.json` | Bot token, signing secret, endpoints |
| `.tmp/crabline/recorders/slack.jsonl` | Append-only accepted API calls (AEH judges read this) |
| `.tmp/crabline/serve.out.log` / `serve.err.log` | Server logs |
| `.tmp/crabline/serve.pid` | Background PID |

### 1.2 Sanity (host loopback)

```bash
curl -s http://127.0.0.1:8787/api/auth.test \
  -H "Authorization: Bearer $(python3 -c "import json; print(json.load(open('.tmp/crabline/ready/slack-server.json'))['botToken'])")"
```

Sandbox traffic must use **`host.openshell.internal:8787`**, not `127.0.0.1`
(inside the sandbox, loopback is the container, not your Mac). The eval sets
`SLACK_API_URL` accordingly.

### 1.3 Keep the Vertex proxy up (tool-aware)

Leave the e2e proxy on `:8000` running. For **this** eval the proxy **must**
bridge OpenAI tools ↔ Anthropic `tool_use` (OpenClaw needs `exec`).

```bash
# Prefer the repo proxy:
.eval-venv/bin/python examples/claude-vertex-proxy.py

# If a LaunchAgent serves /tmp/claude_proxy.py, it must be a copy of the
# tool-aware examples/claude-vertex-proxy.py (contains _openai_tools_to_anthropic).
```

`examples/run-openclaw-crabline-agent-eval.sh` fails fast if `:8000` is unhealthy
or if `/tmp/claude_proxy.py` is an old non-tool build.

---

## Part 2 — The agent use cases

### 2.0 Bootstrap the eval package (required on a fresh machine)

The case tree is **not** committed; generate it locally (same pattern as the
e2e `bootstrap-openclaw-openshell-eval.sh`):

```bash
cd /Users/gziv/Dev/agent-eval-harness
chmod +x examples/bootstrap-openclaw-crabline-agent-eval.sh
./examples/bootstrap-openclaw-crabline-agent-eval.sh
```

That writes:

```text
eval/openclaw-crabline-agent/eval.yaml
eval/openclaw-crabline-agent/cases/<case-name>/{input.yaml,annotations.yaml}
```

Re-running overwrites those demo files (safe; does not delete `eval/runs/`).
`./examples/run-openclaw-crabline-agent-eval.sh` also bootstraps automatically
if `eval.yaml` is missing.

Eval root: `eval/openclaw-crabline-agent/`

#### Slack write cases

| Case | Scope | Purpose |
|------|-------|---------|
| **chat-write** | chat:write | Open DM + post marker |
| **chat-write-threaded** | threaded chat:write | Read parent ts + post threaded reply |

#### Slack read-only cases

| Case | Scope | Purpose |
|------|-------|---------|
| **im-read** | im:read | Seed 3 DMs, list via `conversations.list(types=im)`, verify all found |
| **im-history** | im:history | Seed DM with code, read via `conversations.history`, extract code |
| **channels-read** | channels:read | Seed 3 public channels, list, verify all found |
| **channels-history** | channels:history | Seed public channel with code, read history, extract code |
| **groups-read** | groups:read | Seed 3 private groups, list, verify all found |
| **groups-history** | groups:history | Seed private group with code, read history, extract code |

#### Slack summarization cases (LLM judge)

| Case | Capability | Purpose |
|------|-----------|---------|
| **digest-overnight** | Overnight Slack Digest | Seed urgent + low-priority messages across channels/DMs/groups, agent summarizes and prioritizes |
| **draft-reply** | Draft Slack Reply | Seed a DM, agent drafts a reply without posting |
| **meeting-prep** | Meeting Prep from Slack | Seed calendar event + relevant/irrelevant Slack messages, agent produces prep doc |

#### smolclaw cases

| Case | Mock | Purpose |
|------|------|---------|
| **smolclaw-calendar** | smolclaw Calendar | Find seeded code → create event with code + marker |
| **smolclaw-gmail** | smolclaw Gmail | Find seeded code → send mail with code + marker |

Slack cases use isolated DM users / channel IDs. Gmail/Calendar cases seed via
`annotations.smolclaw_seed` (host loopback) before the agent runs.

### 2.0b Start smolclaw (Gmail + Calendar)

In addition to Crabline (`:8787`) and the Vertex proxy (`:8000`):

```bash
./examples/start-smolclaw.sh
# Gmail  :8001   Calendar :8002
# Installs bingran-you/smolclaw from GitHub into .eval-venv
# (PyPI package name "smolclaw" is a different project — do not pip install that.)
```

Case details (prompts, annotations, seed data) are generated by the bootstrap
script. Run it and inspect the `cases/` directory. Each case has `input.yaml`
(agent prompt) and `annotations.yaml` (ground truth + seed config).

---

## Part 3 — What we are checking

### Deterministic judges

Host-side judges check Crabline recorder, smolclaw API state, or agent response
text. No LLM needed.

| Judge | When | Passes when |
|-------|------|-------------|
| **crabline_accepted_post** | `slack_user` set (write cases) | Recorder has accepted `chat.postMessage` with `expected_text` |
| **crabline_threaded_answer** | `expect_thread_reply` | Threaded post with answer + marker |
| **crabline_channels_listed** | `expected_channels` set | Agent response contains all expected channel/user IDs |
| **crabline_read_code** | `read_only` + `expected_code` | Agent response contains the seeded code |
| **smolclaw_calendar_event** | `smolclaw_kind == calendar` | Primary calendar has event with code + marker |
| **smolclaw_gmail_message** | `smolclaw_kind == gmail` | Gmail has message with code + marker |
| **no_message_posted** | `draft_evaluation` | No agent `chat.postMessage` in recorder (draft-reply only) |
| **used_exec_tool** | always | Trajectory includes real `exec` |
| **response_received** | always | Non-empty final agent response |

### LLM judges

Enabled with `EVAL_LLM_JUDGES=1`. The judge model is configured in `eval.yaml`
under `models.judge`. Used for summarization cases where deterministic checks
can't assess quality.

| Judge | When | Evaluates |
|-------|------|-----------|
| **digest_quality** | `digest_evaluation` | All messages present with channel IDs + timestamps, urgency correctly classified, no fabricated actions |
| **draft_reply_quality** | `draft_evaluation` | Draft addresses the question, covers key points, professional tone, no fabricated info |
| **meeting_prep_quality** | `meeting_prep_evaluation` | Prep doc references calendar agenda, relevant topics present, irrelevant topics completely absent, no filler sections |

The LLM judge receives the agent's response + ground truth from annotations
and evaluates against a rubric prompt. Returns pass/fail.

Implementations: `agent_eval/openshell/crabline_score.py`,
`agent_eval/openshell/smolclaw_score.py`. LLM judge prompts are inline in `eval.yaml`.
---

## Part 4 — Bash runner (recommended)

Wrapper script (same pattern as Phase 1.5 CLI Crabline eval):

```text
examples/run-openclaw-crabline-agent-eval.sh
```

What it does:

1. Requires `.eval-venv` and Crabline ready file
2. Checks `:8000` health and (best-effort) tool-aware Vertex proxy
3. Exports `SLACK_BOT_TOKEN` from the ready file into the case env
4. Sets `CRABLINE_RECORDER`, OpenShell image/policy/provider, `AGENT_EVAL_RUNS_DIR`
5. Runs `python -m agent_eval.openshell.run` (LLM judges off by default)
6. Tees a log under `.tmp/aeh-<RUN_ID>.log`

### 4.1 Run all cases

**Terminal A:** Crabline (`./examples/start-crabline-slack.sh`)  
**Terminal B:** smolclaw (`./examples/start-smolclaw.sh`)  
**Terminal C:** Vertex proxy (tool-aware)  
**Terminal D:**

```bash
cd /Users/gziv/Dev/agent-eval-harness
./examples/run-openclaw-crabline-agent-eval.sh

# Deterministic cases only (Slack read-only):
#   ./examples/run-openclaw-crabline-agent-eval.sh --cases im-read im-history channels-read channels-history groups-read groups-history

# Summarization cases with LLM judges:
#   EVAL_LLM_JUDGES=1 ./examples/run-openclaw-crabline-agent-eval.sh --cases digest-overnight draft-reply meeting-prep

# Full suite with LLM judges:
#   EVAL_LLM_JUDGES=1 ./examples/run-openclaw-crabline-agent-eval.sh
```

Optional flags are passed through to AEH:

```bash
./examples/run-openclaw-crabline-agent-eval.sh --keep-sandbox
./examples/run-openclaw-crabline-agent-eval.sh --cases chat-write channels-read
```

Env overrides (optional):

| Variable | Default |
|----------|---------|
| `OPENSHELL_GATEWAY_ENDPOINT` | `https://localhost:17670` |
| `AGENT_EVAL_OPENSHELL_IMAGE` | Quay OpenClaw `latest` |
| `AGENT_EVAL_OPENSHELL_POLICY` | `deploy/openshell/eval-policy.yaml` |
| `AGENT_EVAL_OPENSHELL_PROVIDER` | `inference` |
| `CRABLINE_AGENT_EVAL_RUNS_DIR` | `eval/openclaw-crabline-agent/eval/runs` |
| `RUN_ID` | `crabline-agent-YYYYMMDD-HHMMSS` |
| `MODEL` | `inference/claude-sonnet-4` |
| `EVAL_LLM_JUDGES` | `0` (set to `1` to enable LLM judges for summarization cases) |

Expected log highlights:

- `Staging workspaces…`
- Host seed lines (Crabline channel/ts, smolclaw event/message)
- `Creating sandbox e-…-XXX`
- `Harvested N events from OpenClaw trajectory…`
- `Scoring 13 cases with … judges…`
- `REPORT: …/report.html`
- `REGRESSIONS: 0` when all judges pass

### 4.2 Equivalent manual invocation

```bash
cd /Users/gziv/Dev/agent-eval-harness

# After start-crabline-slack.sh — export token the same way the script does
export SLACK_BOT_TOKEN="$(python3 -c "import json; print(json.load(open('.tmp/crabline/ready/slack-server.json'))['botToken'])")"
export CRABLINE_RECORDER="$PWD/.tmp/crabline/recorders/slack.jsonl"
export CRABLINE_READY_FILE="$PWD/.tmp/crabline/ready/slack-server.json"
export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
export AGENT_EVAL_OPENSHELL_IMAGE='quay.io/aipcc/base-images/agentic/openclaw:latest'
export AGENT_EVAL_OPENSHELL_POLICY="$PWD/deploy/openshell/eval-policy.yaml"
export AGENT_EVAL_OPENSHELL_PROVIDER=inference
export AGENT_EVAL_RUNS_DIR="$PWD/eval/openclaw-crabline-agent/eval/runs"

RUN_ID="crabline-agent-$(date +%Y%m%d-%H%M%S)"

.eval-venv/bin/python -m agent_eval.openshell.run \
  --config "$PWD/eval/openclaw-crabline-agent/eval.yaml" \
  --model inference/claude-sonnet-4 \
  --run-id "$RUN_ID" \
  --no-llm-judges \
  2>&1 | tee "$PWD/.tmp/aeh-${RUN_ID}.log"
```

---

## Part 5 — Check the results

### 5.1 Report + summary

After a successful script run:

```text
eval/openclaw-crabline-agent/eval/runs/openclaw-crabline-agent/<RUN_ID>/
  report.html          # open in a browser
  summary.yaml         # pass rates / regressions
  collection.json
  cases/<case-name>/
    events.json                      # AEH trajectory
    openclaw-trajectory-events.jsonl # raw OpenClaw export
    stdout.log                       # agent-exec JSON envelope
    run_result.json
    crabline-seed.json               # seeded cases only
    output/
      response.txt
      crabline-hits.jsonl            # matching recorder posts (write cases)
```

```bash
RUN_ID=crabline-agent-REPLACE   # from the script output
RUN=eval/openclaw-crabline-agent/eval/runs/openclaw-crabline-agent/$RUN_ID

open "$RUN/report.html"          # macOS
cat "$RUN/summary.yaml"
```

Expect every listed judge at **100%** pass rate and `REGRESSIONS: 0`.

### 5.2 Trajectories (prove real `exec`, not hallucination)

```bash
.eval-venv/bin/python - <<PY
import json
from pathlib import Path
run = Path("$RUN")
for case_dir in sorted((run / "cases").iterdir()):
    case = case_dir.name
    events_file = case_dir / "events.json"
    if not events_file.exists():
        continue
    events = json.loads(events_file.read_text())
    tools = []
    for e in events:
        if e.get("type") == "assistant":
            for t in e.get("tools") or []:
                cmd = (t.get("input") or {}).get("command", "")[:80]
                tools.append(cmd.replace("\n", " "))
    response_file = case_dir / "output" / "response.txt"
    response = response_file.read_text().strip()[:100] if response_file.exists() else "(none)"
    print(f"{case}: exec_calls={len(tools)}")
    for c in tools:
        print(f"  {c}")
    print(f"  response={response}")
    print()
PY
```

Healthy patterns:

| Case | Typical `exec` sequence |
|------|-------------------------|
| chat-write | `conversations.open` → `chat.postMessage` (marker) |
| im-read | `conversations.list(types=im)` |
| im-history | `conversations.history(channel=DCASE003)` |
| chat-write-threaded | `conversations.open` → `history` → `postMessage` with `thread_ts` |
| channels-read | `conversations.list(types=public_channel)` |
| channels-history | `conversations.history(channel=CCASE006)` |
| groups-read | `conversations.list(types=private_channel)` |
| groups-history | `conversations.history(channel=GCASE008)` |
| digest-overnight | `conversations.list(all types)` → `conversations.history` on each |
| draft-reply | `conversations.history(channel=DDRAFT012)` |
| meeting-prep | `GET calendar events` → `conversations.list` → `conversations.history` on each |

### 5.3 Isolation check

Each case uses unique channel IDs or user IDs to avoid cross-case pollution.
Re-running without restarting Crabline accumulates messages — restart Crabline
for a clean slate if needed.

---

## Part 6 — Keep sandboxes / cleanup

```bash
./examples/run-openclaw-crabline-agent-eval.sh --keep-sandbox
# log: Kept sandbox e-…. copy names from the log

export OPENSHELL_GATEWAY_ENDPOINT='https://localhost:17670'
openshell sandbox list
openshell sandbox connect e-REPLACE-001

# when done
openshell sandbox delete e-REPLACE-001
# …

# stop Crabline
kill "$(cat .tmp/crabline/serve.pid)" 2>/dev/null
# stop proxy with Ctrl-C in its terminal
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `missing …/ready/slack-server.json` | `./examples/start-crabline-slack.sh` |
| Sandbox curl to Crabline fails / timeout | Policy must allow `host.openshell.internal:8787`; Crabline bound `0.0.0.0:8787` (not `127.0.0.1` only) |
| Agent invents tool results / never calls `exec` | Proxy on `:8000` is not tool-aware — use `examples/claude-vertex-proxy.py` (or refresh `/tmp/claude_proxy.py`) |
| case-002/003 fail history / wrong code | Confirm seed in log; restart Crabline if DM history is polluted; check `crabline-seed.json` |
| `crabline_accepted_post` false but agent claims ok | Inspect recorder JSONL and `crabline-hits.jsonl` — judges trust the recorder, not the final prose |
| Empty `events.json` | Same as e2e: need trajectory harvest; check log for harvest warnings |
| OpenShell / inference / Podman issues | Fix via the **e2e prerequisite guide** first |

---

## Quick reference — related files

| File | Role |
|------|------|
| [openshell-openclaw-e2e.md](./openshell-openclaw-e2e.md) | Prerequisite stack (OpenShell + Quay + Vertex proxy) |
| `examples/start-crabline-slack.sh` | Host Crabline Slack mock |
| `examples/start-smolclaw.sh` | Host smolclaw Gmail (:8001) + Calendar (:8002) |
| `examples/bootstrap-openclaw-crabline-agent-eval.sh` | Generate agent `eval.yaml` + 13 cases |
| `examples/bootstrap-openclaw-crabline-eval.sh` | Generate Phase 1.5 CLI eval package |
| `examples/run-openclaw-crabline-agent-eval.sh` | Full agent suite runner |
| `examples/run-openclaw-crabline-eval.sh` | Phase 1.5 CLI (no agent) Crabline canary suite |
| `examples/claude-vertex-proxy.py` | Tool-aware Vertex proxy |
| `eval/openclaw-crabline-agent/` | Generated locally (not committed) |
| `agent_eval/openshell/smolclaw_seed.py` | Host Gmail/Calendar needles |
| `agent_eval/openshell/smolclaw_score.py` | Gmail/Calendar judges |
| `agent_eval/openshell/run.py` | AEH OpenShell orchestrator (+ seed hook) |
| `agent_eval/openshell/crabline_seed.py` | Host seed for 002/003 |
| `agent_eval/openshell/crabline_score.py` | Recorder judges |
| `deploy/openshell/eval-policy.yaml` | Allows Crabline + inference egress |

