---
title: OpenShell Backend
description: Run evaluations in policy-enforced OpenShell sandboxes with OpenClaw
---

# OpenShell Backend

The OpenShell backend runs evaluations inside OpenShell policy-enforced sandboxes
using OpenClaw as the agent.

## Full e2e guide

For a clean-slate walkthrough (Vertex proxy, Quay OpenClaw image, three demo
cases, `--keep-sandbox`, and how to verify AEH + OpenShell + OpenClaw):

**[docs/openshell-openclaw-e2e.md](../../docs/openshell-openclaw-e2e.md)**

## When to use OpenShell

- Policy enforcement (Landlock filesystem + network egress)
- OpenClaw evaluations (trajectories, session/SQLite state)
- Isolation from host Claude Code / Harbor

## Quick start (after following the e2e guide prerequisites)

```bash
export OPENSHELL_GATEWAY_ENDPOINT="http://[::1]:17670"
export AGENT_EVAL_OPENSHELL_IMAGE=quay.io/aipcc/base-images/agentic/openclaw:latest
export AGENT_EVAL_OPENSHELL_POLICY="$PWD/deploy/openshell/eval-policy.yaml"
export AGENT_EVAL_OPENSHELL_PROVIDER=inference
export AGENT_EVAL_RUNS_DIR="$PWD/eval/openclaw-openshell/eval/runs"

.eval-venv/bin/python -m agent_eval.openshell.run \
  --config eval/openclaw-openshell/eval.yaml \
  --model inference/claude-sonnet-4 \
  --run-id test-001
```

Add `--keep-sandbox` to leave OpenShell sandboxes running for inspection.
