---
title: OpenShell Backend
description: Run evaluations in policy-enforced OpenShell sandboxes with OpenClaw
---

# OpenShell Backend

The OpenShell backend runs evaluations inside [OpenShell](https://github.com/NVIDIA/OpenShell)
policy-enforced sandboxes using [OpenClaw](https://github.com/openclaw/openclaw) as the agent.
This provides security isolation with configurable filesystem and network policies.

## When to Use OpenShell

Choose OpenShell when you need:

- **Policy enforcement** — Landlock/seccomp filesystem policies, network egress control
- **OpenClaw evaluations** — OpenClaw-specific features (trajectories, session state)
- **Security isolation** — Strict sandboxing for untrusted code execution
- **Reproducibility** — Containerized execution with pinned versions

For simpler cases, [local execution](./eval-run.md) or [Harbor](./harbor.md) may be sufficient.

## Prerequisites

1. **OpenShell CLI** installed:
   ```bash
   # Follow OpenShell installation docs
   openshell --version
   ```

2. **Gateway running** (local Docker or remote OpenShift):
   ```bash
   # Local development
   openshell gateway start --name local
   
   # Or remote OpenShift gateway
   openshell gateway add https://<route> --name openshift
   ```

3. **Sandbox image** with OpenClaw:
   ```bash
   podman build -f deploy/openshell/Containerfile \
     --build-arg OPENCLAW_VERSION=1.2.3 \
     -t quay.io/<org>/openclaw-sandbox:v1.0.0
   podman push quay.io/<org>/openclaw-sandbox:v1.0.0
   ```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSHELL_GATEWAY_ENDPOINT` | Gateway gRPC endpoint | `https://127.0.0.1:17670` |
| `AGENT_EVAL_OPENSHELL_IMAGE` | Sandbox image with OpenClaw | **Required** |
| `AGENT_EVAL_OPENSHELL_POLICY` | Path to policy YAML | None |
| `AGENT_EVAL_OPENSHELL_PROVIDER` | Provider name for model auth | None |
| `AGENT_EVAL_OPENSHELL_KEEP_RUN` | Keep sandbox after trial (`1`) | `0` |

## Quick Start

```bash
# Set required environment
export OPENSHELL_GATEWAY_ENDPOINT=https://127.0.0.1:17670
export AGENT_EVAL_OPENSHELL_IMAGE=quay.io/<org>/openclaw-sandbox:v1.0.0
export AGENT_EVAL_OPENSHELL_POLICY=deploy/openshell/eval-policy.yaml
export ANTHROPIC_API_KEY=<key>

# Run evaluation
/eval-run --runner openshell --model anthropic/claude-sonnet-4-6
```

Or directly via Python module:

```bash
python -m agent_eval.openshell.run \
  --config eval.yaml \
  --model anthropic/claude-sonnet-4-6 \
  --run-id test-001
```

## Configuration

### eval.yaml

```yaml
runner:
  type: openclaw
  effort: medium  # maps to --thinking medium

models:
  skill: anthropic/claude-sonnet-4-6

execution:
  prompt: "Your prompt here..."
  timeout: 300

dataset:
  path: cases

outputs:
  - path: output/

judges:
  - name: has_output
    check: "exit 0 if outputs.get('files') else exit 1"
```

### Policy Configuration

OpenShell's default policy denies all egress. The `eval-policy.yaml` in
`deploy/openshell/` allows model API access:

```yaml
version: 1

filesystem_policy:
  read_write:
    - /sandbox
    - /tmp

network_policies:
  anthropic:
    name: anthropic-api
    endpoints:
      - host: api.anthropic.com
        port: 443
        protocol: rest
        access: full
    binaries:
      - path: /usr/bin/node
```

## Credentials

### Option 1: OpenShell Providers (Recommended)

Credentials stay in the gateway; sandbox never sees them:

```bash
openshell provider create --name anthropic --type anthropic --from-existing
export AGENT_EVAL_OPENSHELL_PROVIDER=anthropic
```

### Option 2: Environment Injection

The backend injects `ANTHROPIC_API_KEY` via `sandbox exec --env`.
OpenClaw uses `--auth-env-only` to read keys from environment.

## Pipeline

The OpenShell backend handles the complete evaluation pipeline:

1. **Workspace staging** — `workspace.py` prepares case directories
2. **Sandbox lifecycle** — Create, upload, execute, download, delete
3. **Artifact collection** — `collect.py` gathers outputs
4. **Scoring** — `score.py judges` runs all configured judges
5. **Report generation** — `report.py` creates HTML report
6. **Regression detection** — Threshold checks (if configured)

Pairwise comparison and MLflow logging run after the backend completes.

## Trajectory Capture

OpenClaw session state is captured to `<run>/cases/<case>/trajectory/`:

- Conversation history
- Tool call arguments and results
- MCP server interactions

To make trajectories available to judges, add to outputs:

```yaml
outputs:
  - path: trajectory
```

## Debugging

### Keep Sandboxes

```bash
export AGENT_EVAL_OPENSHELL_KEEP_RUN=1
# Or: --keep-sandbox flag

# Reconnect after run
openshell sandbox connect aeh-case-001-abc123
```

### Check Gateway

```bash
openshell gateway list
openshell gateway status local
```

### View Sandbox Logs

```bash
openshell sandbox logs aeh-case-001-abc123
```

## Parallelism

Run multiple cases concurrently:

```bash
python -m agent_eval.openshell.run \
  --config eval.yaml \
  --model anthropic/claude-sonnet-4-6 \
  -n 4  # 4 concurrent sandboxes
```

## v1 Limitations

Current version supports:
- Prompt mode only (skill mode deferred)
- `str.format()` templates (Jinja `{{ input.* }}` deferred)
- `system_prompt` ignored with warning
- `max_budget_usd` ignored with warning

## OpenShift Deployment

See `deploy/openshell/README.md` for full OpenShift deployment instructions.

## Comparison with Other Backends

| Feature | Local | Harbor | OpenShell |
|---------|-------|--------|-----------|
| Sandboxing | None | Container | Policy-enforced |
| Agent | Any | Harbor agents | OpenClaw only |
| Network policy | None | None | Landlock/egress |
| Filesystem policy | None | Container boundaries | Landlock |
| Trajectory capture | No | Limited | Full |
| Infrastructure | None | Docker/K8s | OpenShell Gateway |

## See Also

- [Harbor Guide](./harbor.md) — Containerized execution with Harbor
- [EvalHub Guide](./evalhub.md) — Platform-managed execution
- [Headless Execution](./headless.md) — Tool interception for unattended runs
