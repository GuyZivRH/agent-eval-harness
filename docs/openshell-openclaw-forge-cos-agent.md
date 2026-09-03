# AEH + OpenShell + OpenClaw — COS Agent Evaluation Guide

This guide covers evaluating the **Chief of Staff (COS) agent** from the
forge-agent-catalog. The COS agent is installed as a CLAW package into OpenClaw
inside an OpenShell sandbox, and evaluated using its skills via slash commands.

It assumes the prerequisite stack already works on your machine.

## Prerequisite

Complete and leave healthy:

**[AEH + OpenShell + OpenClaw (Quay) — end-to-end guide](./openshell-openclaw-e2e.md)**

That means you already have:

- Podman + OpenShell gateway (`openshell status` Connected + mTLS)
- `.eval-venv` with `pip install -e '.[anthropic]'` (+ `fastapi` / `uvicorn`)
- Host Vertex proxy on `:8000` wired to OpenShell `inference` provider
- Quay image pull + `deploy/openshell/eval-policy.yaml`

This eval **reuses** that stack. It adds host Crabline on `:8787` and
smolclaw on `:8001`/`:8002` for mock data.

---

## What this evaluates

The COS agent is Forge's permanent executive advisor. It is installed as a
CLAW package (`resources/forge-agent-catalog/chief-of-staff/`) into OpenClaw via `openclaw claws add`.
The agent's skills are invoked using slash commands.

Two test cases:

| Case | Skill | Prompt |
|------|-------|--------|
| daily-briefing | `/daily-briefing` (exists in COS) | Invokes the daily briefing skill |
| analysis-panel | `/analysis` (not yet implemented) | Invokes a future analysis skill |

---

## Architecture

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────────────┐
│ AEH (host)   │────▶│ OpenShell       │────▶│ Quay OpenClaw sandbox    │
│ openshell.run│     │ gateway +       │     │ COS agent installed via  │
│ + scene seed │     │ sandbox create  │     │ openclaw claws add       │
│ + LLM judges │     │                 │     │ skills via /slash-cmd    │
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

### COS agent installation flow

1. Sandbox created with eval policy + inference provider
2. `chief-of-staff/` CLAW package uploaded to `/sandbox/`
3. `openclaw claws add` validates and installs the package
4. Workspace bootstrapped at `/sandbox/.openclaw/workspace-chief-of-staff/`
5. Agent identity (SOUL.md) and workspace files (AGENTS.md, SKILL.md, etc.) loaded
6. Skills invoked via `/skill-name` slash commands in the prompt

---

## Extra installs

### COS agent package

Clone the forge-agent-catalog into `resources/`:

```bash
git clone git@github.com:rh-forge/forge-agent-catalog.git resources/forge-agent-catalog
```

To update the COS agent later:

```bash
cd resources/forge-agent-catalog && git pull && cd -
```

### Node/npm + smolclaw

Same as the Crabline guide:

```bash
node --version   # v20+
npm --version

.eval-venv/bin/pip install 'git+https://github.com/bingran-you/smolclaw.git'
```

---

## Part 1 — Start the host services

**Terminal A:** Crabline Slack mock

```bash
./examples/start-crabline-slack.sh
```

**Terminal B:** smolclaw Gmail + Calendar

```bash
./examples/start-smolclaw.sh
```

**Terminal C:** Vertex proxy (must be tool-aware, with opus model)

```bash
.eval-venv/bin/python examples/claude-vertex-proxy.py
```

Verify `claude-opus-4-6` is in the proxy model list.

### Model configuration

Two models are configured independently in `eval.yaml`:

- **Agent model**: Set in `eval.yaml` under `runner.providers.inference.models`
  and passed via `--model` on the run script (or `MODEL=` env var). The COS
  agent uses this inside the sandbox. Routed through OpenShell's `inference`
  provider → `inference.local` → host Vertex proxy on `:8000` → Google Vertex AI.
- **Judge model**: Set in `eval.yaml` under `models.judge`. Used by LLM rubric
  judges to score the agent's output. Runs on the host (not in the sandbox) via
  the Anthropic SDK with direct Vertex API access. Must be a full model version
  reference (e.g. `claude-sonnet-4-5-20250514`).

---

## Part 2 — Bootstrap the eval package

```bash
chmod +x examples/bootstrap-openclaw-forge-cos-agent-eval.sh
./examples/bootstrap-openclaw-forge-cos-agent-eval.sh
```

This creates `eval/openclaw-forge-cos-agent/` with:
- `eval.yaml` — config with COS agent identity and slash command prompts
- `chief-of-staff/` — the CLAW package (copied from `examples/`)
- `scenes/monday-acquisition.yaml` — seeded messages
- `cases/daily-briefing/` — `/daily-briefing` case
- `cases/analysis-panel/` — `/analysis` case

---

## Part 3 — Run the eval

```bash
./examples/run-openclaw-forge-cos-agent-eval.sh
```

Or with a specific model:

```bash
MODEL=inference/claude-opus-4-6 ./examples/run-openclaw-forge-cos-agent-eval.sh
```

The run script:
1. Starts fresh mocks (Crabline + smolclaw)
2. Seeds the monday-acquisition scene
3. For each case: creates sandbox, uploads + installs COS package, runs `/skill-name`
4. Scores with LLM judges
5. Generates report

### Image version

Uses `quay.io/aipcc/base-images/agentic/openclaw:0.0.1-1787752534` which has
OpenClaw `2026.8.1-beta.3` (supports `claws validate` and `claws add`).

---

## Part 4 — Check the results

```bash
RUN_ID=forge-cos-agent-REPLACE
RUN=eval/openclaw-forge-cos-agent/eval/runs/forge-cos-agent/$RUN_ID

xdg-open "$RUN/report.html"
cat "$RUN/summary.yaml"
```

---

## Known limitations

- **Skill discovery**: OpenClaw's skill registry does not auto-discover workspace
  skills in the current beta versions. Skills are invoked via the `/skill-name`
  slash command (scoped exception) which reads the SKILL.md file directly.
- **COS skill dependencies**: The `daily-briefing` SKILL.md is tightly coupled
  to the Forge deployment (hardcoded `gog` CLI, Forge relay endpoints). The agent
  will report missing dependencies. This is a known issue being addressed with
  the COS maintainers.
- **`/analysis` skill**: Does not exist yet in the COS package. The
  analysis-panel case tests a future capability.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `failed to connect to gateway` | Use `https://127.0.0.1:17670` not `https://localhost:17670` |
| `ContainerExited: code 1` | Restart OpenShell gateway: `systemctl --user restart openshell-gateway` |
| `claws add` fails | Ensure `OPENCLAW_EXPERIMENTAL_CLAWS=1` is set |
| Agent reports missing `gog` CLI | Expected — COS skill is coupled to Forge deployment |
| Empty `openclaw skills list` | Known beta limitation — skills CLI is incomplete |

---

## Quick reference

| File | Role |
|------|------|
| [openshell-openclaw-e2e.md](./openshell-openclaw-e2e.md) | Prerequisite stack |
| `resources/forge-agent-catalog/chief-of-staff/` | COS CLAW package (from forge-agent-catalog) |
| `examples/bootstrap-openclaw-forge-cos-agent-eval.sh` | Generate eval package |
| `examples/run-openclaw-forge-cos-agent-eval.sh` | Wrapper run script |
| `examples/start-crabline-slack.sh` | Host Crabline Slack mock |
| `examples/start-smolclaw.sh` | Host smolclaw Gmail + Calendar |
| `examples/claude-vertex-proxy.py` | Tool-aware Vertex proxy |
