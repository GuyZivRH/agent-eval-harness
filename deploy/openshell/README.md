# OpenShell Deployment

This directory contains deployment artifacts for running agent-eval-harness
evaluations inside OpenShell sandboxes.

## Contents

- `Containerfile` - Builds the sandbox image with OpenClaw pre-installed
- `eval-policy.yaml` - OpenShell policy allowing model API access

## Quick Start

### 1. Build Sandbox Image

```bash
# Pin the OpenClaw version for reproducible evaluations
podman build -f deploy/openshell/Containerfile \
  --build-arg OPENCLAW_VERSION=1.2.3 \
  -t quay.io/<org>/openclaw-sandbox:v1.0.0

podman push quay.io/<org>/openclaw-sandbox:v1.0.0
```

### 2. Start Local Gateway (Development)

```bash
# Start Docker-based gateway
openshell gateway start --name local

# Verify
openshell gateway list
```

### 3. Run Evaluation

```bash
export OPENSHELL_GATEWAY_ENDPOINT=https://127.0.0.1:17670
export AGENT_EVAL_OPENSHELL_IMAGE=quay.io/<org>/openclaw-sandbox:v1.0.0
export AGENT_EVAL_OPENSHELL_POLICY=deploy/openshell/eval-policy.yaml
export ANTHROPIC_API_KEY=<key>

python -m agent_eval.openshell.run \
  --config eval.yaml \
  --model anthropic/claude-sonnet-4-6 \
  --run-id test-001
```

## OpenShift Deployment

### Prerequisites

1. Install agent-sandbox CRD:
   ```bash
   kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/latest/download/manifest.yaml
   ```

2. Create namespace:
   ```bash
   oc create ns openshell
   ```

3. Add SCC (development only - use restricted SCC in production):
   ```bash
   oc adm policy add-scc-to-user privileged -z openshell-sandbox -n openshell
   ```

4. Install OpenShell Gateway:
   ```bash
   helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart -n openshell \
     --set server.disableTls=true  # Dev only
   ```

5. Register gateway:
   ```bash
   openshell gateway add https://<route> --name openshift
   ```

## Credentials

### Option 1: OpenShell Providers (Recommended)

Gateway proxies model requests; credentials never enter sandbox.

```bash
openshell provider create --name anthropic --type anthropic --from-existing
export AGENT_EVAL_OPENSHELL_PROVIDER=anthropic
```

### Option 2: Environment Injection

The backend passes `ANTHROPIC_API_KEY` via `sandbox exec --env`.
OpenClaw uses `--auth-env-only` to read keys from environment.

```bash
export ANTHROPIC_API_KEY=<key>
# Backend injects automatically
```

## Debugging

Keep sandboxes after trial for debugging:

```bash
export AGENT_EVAL_OPENSHELL_KEEP_RUN=1
python -m agent_eval.openshell.run ...

# Reconnect to sandbox
openshell sandbox connect aeh-case-001-abc123
```

## Policy Customization

The default `eval-policy.yaml` allows:
- Read-only access to system directories
- Read-write access to `/sandbox` and `/tmp`
- Network egress to Anthropic, OpenAI, and Vertex AI APIs

For restricted evaluations, modify `network_policies` to block specific endpoints
or use OpenShell providers for credential isolation.
