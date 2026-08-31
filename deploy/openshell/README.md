# OpenShell sandbox images

Build helpers for optional **local** OpenClaw sandbox images
(`Containerfile`, `Containerfile.openclaw`).

For the **supported e2e path** (AEH + OpenShell + Quay OpenClaw + Vertex proxy),
follow:

**[docs/openshell-openclaw-e2e.md](../../docs/openshell-openclaw-e2e.md)**

That guide covers proxy setup, OpenShell inference wiring, the three demo cases,
`--keep-sandbox` inspection, and how to prove the stack.

The Landlock policy required for the Quay image (`/opt/openclaw` read-only) is:

`deploy/openshell/eval-policy.yaml`
