# AEH + OpenShell + OpenClaw — Forge Evaluation Rubrics Guide

This guide covers the **Forge evaluation rubrics** — scoring a Chief of Staff
agent that produces an executive morning briefing from Slack, Gmail, and
Calendar data. It uses scene-based seeding (one shared inbox seeded once) and
LLM rubric judges scored 1-5.

It assumes the previous guide already works on your machine.

## Prerequisite

Complete and leave healthy:

**[AEH + OpenShell + OpenClaw (Quay) — end-to-end guide](./openshell-openclaw-e2e.md)**

That means you already have:

- Podman + OpenShell gateway (`openshell status` Connected + mTLS)
- `.eval-venv` with `pip install -e '.[anthropic]'` (+ `fastapi` / `uvicorn`)
- Host Vertex proxy on `:8000` wired to OpenShell `inference` → `inference.local`
- Quay image pull + `deploy/openshell/eval-policy.yaml` (includes `/opt/openclaw`)

This eval **reuses** that stack. It adds host Crabline on `:8787` and
smolclaw on `:8001`/`:8002`.

---

## What this evaluates

The Forge "Chief of Staff" agent reads an executive's Slack channels, Gmail
inbox, and Calendar, then produces:

1. **Morning briefing** — Top of Mind / FYI / Looking Ahead sections with
   grouped cards
2. **Analysis panel** — detailed synthesis for a specific card with sources,
   timeline, and recommendation

The evaluation scores these outputs against three rubrics from the Forge
evaluation rubrics document:

| Rubric | Dimensions | Scored on |
|--------|-----------|-----------|
| Task Prioritization | Recall, Precision, Relevance | morning-briefing case |
| Data Connection | Precision, Recall | morning-briefing case |
| Analysis Panel | Accuracy, Independent Judgment, Citations | analysis-panel case |

---

## Architecture

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────────────┐
│ AEH (host)   │────▶│ OpenShell       │────▶│ Quay OpenClaw sandbox    │
│ openshell.run│     │ gateway +       │     │ openclaw agent exec      │
│ + scene seed │     │ sandbox create  │     │ exec → curl → mocks      │
│ + LLM judges │     │                 │     │ image: quay.io/.../openclaw
└──────┬───────┘     └────────┬────────┘     └────────────┬─────────────┘
       │                      │                           │
       │ seed once            │  inference.local (HTTPS)  │
       ▼                      ▼                           │
┌─────────────────┐  ┌─────────────────┐                  │
│ Host Crabline   │  │ Host FastAPI    │◀── host.openshell.internal:8000
│ Slack mock      │  │ Vertex proxy    │
│ :8787 (HTTP)    │  │ :8000 (HTTP)    │
├─────────────────┤  └────────┬────────┘
│ Host smolclaw   │           ▼
│ Gmail :8001     │      Google Vertex
│ Calendar :8002  │
└─────────────────┘
```

### Scene-based seeding

Unlike per-case seeding, the Forge eval uses **scene-based seeding**: a single
scene file under `scenes/` defines messages across Slack, Gmail, and Calendar.
The scene is seeded **once** before the case loop via `_setup_scene()` in
`run.py`. All cases run against the same seeded data.

A scene should represent a realistic executive inbox — mix urgent decisions
with noise, cross-channel workstreams, conflicting information, and hidden
constraints to test prioritization and synthesis.

---

## File structure

```
eval/openclaw-forge-agent/
  eval.yaml                              # Config: scene name, runner, judges
  scenes/
    <scene-name>.yaml                    # Seeded messages (Slack, Gmail, Calendar)
  cases/
    <case-name>/
      input.yaml                         # Agent prompt
      annotations.yaml                   # Ground truth for judges
```

The bootstrap ships with `monday-acquisition` as the default scene and two
cases (`morning-briefing`, `analysis-panel`). Add new scenes and cases by
editing the bootstrap script.

---

## Extra installs (after the e2e venv is working)

You do **not** need a new Python venv. Confirm the e2e packages, then add
**Node/npm** for Crabline (host only).

### Python (same `.eval-venv`)

```bash
cd /path/to/agent-eval-harness

.eval-venv/bin/python -c "import agent_eval, yaml, jinja2, anthropic; print('aeh ok')"
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

If missing on Fedora: `sudo dnf install nodejs npm`.

---

## Part 1 — Start the host services

**Terminal A:** Crabline Slack mock

```bash
./examples/start-crabline-slack.sh
```

**Terminal B:** smolclaw Gmail + Calendar

```bash
./examples/start-smolclaw.sh
# Gmail :8001   Calendar :8002
```

**Terminal C:** Vertex proxy (must be tool-aware)

```bash
.eval-venv/bin/python examples/claude-vertex-proxy.py
```

Leave all three running for the eval.

---

## Part 2 — Bootstrap the eval package

```bash
chmod +x examples/bootstrap-openclaw-forge-agent-eval.sh
./examples/bootstrap-openclaw-forge-agent-eval.sh
```

This writes/overwrites `eval.yaml`, `scenes/monday-acquisition.yaml`, and
both case directories. Does not touch `eval/runs/`.

---

## Part 3 — Test cases and scenes

Each case under `cases/` has an `input.yaml` (agent prompt) and
`annotations.yaml` (ground truth for judges). Cases are scored by
LLM rubric judges (1-5) plus two deterministic judges that run on every case:

- `used_exec_tool` (bool) — agent called `exec` at least once
- `response_received` (bool) — agent produced a non-empty response

### Default cases (bootstrap)

| Case | Prompt | LLM judges |
|------|--------|------------|
| **morning-briefing** | Read all Slack/Gmail/Calendar, produce Top of Mind / FYI / Looking Ahead briefing | prioritization (recall, precision, relevance) + connection (precision, recall) |
| **analysis-panel** | Produce analysis panel for a specific card | accuracy, independent judgment, citations |

### Default scene: monday-acquisition

The `monday-acquisition` scene seeds 23 messages before the cases run:

- **14 Slack messages** across 5 channels
- **6 Gmail messages** (acquisition terms, customer escalation, newsletter,
  vendor contract, budget request + revision)
- **3 Calendar events** (deadline, earnings prep, interview)

Key test patterns embedded in the scene:
- Mixed urgency (urgent items alongside noise)
- Non-VIP sender with time-critical content
- Stale critical item with no reply
- Cross-channel grouping (same topic in email + Slack)
- Same sender, different topics (must not merge)
- Follow-up chain across email + Slack
- Conflicting information across sources
- Hidden constraint in a follow-up message

To add a new scene, create a YAML file under `scenes/` with `crabline_seeds`
and `smolclaw_seeds` sections, update `eval.yaml`'s `scene:` field, and add
matching cases with appropriate annotations.

---

## Part 4 — Run the eval

```bash
cd /path/to/agent-eval-harness

export SLACK_BOT_TOKEN="$(python3 -c "import json; print(json.load(open('.tmp/crabline/ready/slack-server.json'))['botToken'])")"
export CRABLINE_RECORDER="$PWD/.tmp/crabline/recorders/slack.jsonl"
export CRABLINE_READY_FILE="$PWD/.tmp/crabline/ready/slack-server.json"
export OPENSHELL_GATEWAY_ENDPOINT='https://127.0.0.1:17670'
export AGENT_EVAL_OPENSHELL_IMAGE='quay.io/aipcc/base-images/agentic/openclaw:latest'
export AGENT_EVAL_OPENSHELL_POLICY="$PWD/deploy/openshell/eval-policy.yaml"
export AGENT_EVAL_OPENSHELL_PROVIDER=inference
export AGENT_EVAL_RUNS_DIR="$PWD/eval/openclaw-forge-agent/eval/runs"

RUN_ID="forge-agent-$(date +%Y%m%d-%H%M%S)"

.eval-venv/bin/python -m agent_eval.openshell.run \
  --config "$PWD/eval/openclaw-forge-agent/eval.yaml" \
  --model inference/claude-sonnet-4 \
  --run-id "$RUN_ID" \
  2>&1 | tee "$PWD/.tmp/aeh-${RUN_ID}.log"
```

Or use the wrapper script:

```bash
./examples/run-openclaw-forge-agent-eval.sh
```

### Before re-running

Restart Crabline and smolclaw between runs to clear stale seeded data:

```bash
# Restart Crabline
kill "$(cat .tmp/crabline/serve.pid)" 2>/dev/null
sleep 2
./examples/start-crabline-slack.sh

# Restart smolclaw
kill $(lsof -tiTCP:8001 -sTCP:LISTEN) 2>/dev/null
kill $(lsof -tiTCP:8002 -sTCP:LISTEN) 2>/dev/null
sleep 2
./examples/start-smolclaw.sh
```

---

## Part 5 — Check the results

```bash
RUN_ID=forge-agent-REPLACE   # from the script output
RUN=eval/openclaw-forge-agent/eval/runs/forge-eval-rubrics/$RUN_ID

open "$RUN/report.html"          # or xdg-open on Linux
cat "$RUN/summary.yaml"
```

### Judges and scoring

| Judge | Type | Score | Applied to |
|-------|------|-------|------------|
| prioritization_recall | LLM | 1-5 | morning-briefing |
| prioritization_precision | LLM | 1-5 | morning-briefing |
| prioritization_relevance | LLM | 1-5 | morning-briefing |
| connection_precision | LLM | 1-5 | morning-briefing |
| connection_recall | LLM | 1-5 | morning-briefing |
| analysis_accuracy | LLM | 1-5 | analysis-panel |
| analysis_independent_judgment | LLM | 1-5 | analysis-panel |
| analysis_citations | LLM | 1-5 | analysis-panel |
| used_exec_tool | check | bool | both |
| response_received | check | bool | both |

Each LLM judge returns a **score** (1-5) and a **rationale** explaining why.
The rubric criteria come from the Forge evaluation rubrics document (see
`Evaluation rubrics ideation.md`).

### Thresholds

All LLM judges require `min_mean: 5.0`. Deterministic judges require
`min_pass_rate: 1.0`. Scores below these thresholds flag regressions.

---

## Part 6 — Cleanup

```bash
# Stop Crabline
kill "$(cat .tmp/crabline/serve.pid)" 2>/dev/null

# Stop smolclaw
kill $(lsof -tiTCP:8001 -sTCP:LISTEN) 2>/dev/null
kill $(lsof -tiTCP:8002 -sTCP:LISTEN) 2>/dev/null

# Stop proxy with Ctrl-C in its terminal
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `failed to connect to gateway` | Use `https://127.0.0.1:17670` not `https://localhost:17670` on Linux |
| Empty agent output / all scores 1 | Check sandbox logs; verify proxy is tool-aware (`examples/claude-vertex-proxy.py`) |
| Duplicate seeded messages | Restart Crabline + smolclaw between runs to clear stale data |
| `scene file not found` | Run `./examples/bootstrap-openclaw-forge-agent-eval.sh` to generate files |
| Judge template warnings (`no attribute 'output_content'`) | Agent produced empty response — check sandbox/proxy |
| smolclaw 404 on root | Expected — it serves at `/gmail/v1/` and `/calendar/v3/`, not `/` |

---

## Quick reference — related files

| File | Role |
|------|------|
| [openshell-openclaw-e2e.md](./openshell-openclaw-e2e.md) | Prerequisite stack (OpenShell + Quay + Vertex proxy) |
| [openshell-openclaw-crabline-agent.md](./openshell-openclaw-crabline-agent.md) | Per-case Crabline agent eval (cases 001-005) |
| `examples/bootstrap-openclaw-forge-agent-eval.sh` | Generate eval.yaml + scene + cases |
| `examples/start-crabline-slack.sh` | Host Crabline Slack mock |
| `examples/start-smolclaw.sh` | Host smolclaw Gmail (:8001) + Calendar (:8002) |
| `examples/run-openclaw-forge-agent-eval.sh` | Wrapper run script |
| `examples/claude-vertex-proxy.py` | Tool-aware Vertex proxy |
| `eval/openclaw-forge-agent/eval.yaml` | Eval config with scene + judges |
| `eval/openclaw-forge-agent/scenes/monday-acquisition.yaml` | Scene seed data |
| `agent_eval/openshell/run.py` | Runner with `_setup_scene()` |
| `agent_eval/openshell/crabline_seed.py` | `seed_crabline_for_scene()` |
| `agent_eval/openshell/smolclaw_seed.py` | `seed_smolclaw_for_scene()` |
| `Evaluation rubrics ideation.md` | Source rubric criteria (1-5 scales) |
