# Forge real Slack + M365

End-to-end path for IBM Forge demo data (no Crabline / smolclaw).

Slack OAuth details: **[slack-auth.md](./slack-auth.md)** (redirect URIs, SAW vs local, drafts bearer).

## Secrets (gitignored)

`.tmp/` is gitignored. Put secrets only in `.tmp/forge-real/env`:

### M365
- `M365_ACCESS_TOKEN` — delegated Graph token (via `device_login.sh`)
- App registration: `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`
- User: `M365_USER` (e.g. `tbx-demo2@…`)

### Slack (Forge `forge-outlook-slack` profile)
App OAuth clients (do **not** call the Web API by themselves):
- `FORGE_SLACK_TEAM_ID`
- `FORGE_SLACK_READ_CLIENT_ID` / `FORGE_SLACK_READ_CLIENT_SECRET`
- `FORGE_SLACK_WRITE_CLIENT_ID` / `FORGE_SLACK_WRITE_CLIENT_SECRET`
- `INTEGRATIONS_PROFILE=forge-outlook-slack`

Usable read credential after install (user token `xoxp-…`, not client-credentials):
- `SLACK_READ_USER_TOKEN` — preferred for channel history
- `SLACK_BOT_TOKEN` — alias accepted by `inventory_slack.sh` / OpenClaw
- `SLACK_API_URL` — default `https://slack.com/api/`
- `FORGE_SLACK_CHANNELS` — demo channel names

Auth model (from `rh-forge/forge-saw` + `rh-forge/rust-slack-proxy`): separate
read/write Slack apps mint **user** tokens via OAuth; SAW onboarding stores them
as OpenShell provider `user_token` and injects into slack-read/write sandboxes.
`slack-drafts-bearer` under `/sandbox/persist/.forge-drafts/` is an **inter-service**
bearer for drafts↔writer — not a Slack token. For local eval we talk to
`slack.com` directly with the read `xoxp` token.

Registered SAW redirect (deployed Forge only):

```text
https://<FORGE_ENTRY_HOST>/oauth/callback
```

(`onboarding.toml`: `redirect_uri = "@@FORGE_ENTRY_ORIGIN@@/oauth/callback"`). Local
`http://127.0.0.1:8765/callback` is **not** registered → Slack `redirect_uri` mismatch.

## Steps

1. Device login (M365):
   ```bash
   ./examples/forge-real/device_login.sh
   ```
   Complete the browser prompt as **tbx-demo2** (or the configured `M365_USER`).

2. Slack read token (once per machine / until revoked) — **Install to Workspace**:
   ```bash
   ./examples/forge-real/oauth_slack_read.sh --install   # prints exact steps
   ```
   Then: https://api.slack.com/apps → **Forge Slack Read Proxy** → OAuth & Permissions
   → **Install to Workspace** → copy User OAuth Token (`xoxp-…`) into `.tmp/forge-real/env`:
   ```bash
   export SLACK_READ_USER_TOKEN='xoxp-...'
   export SLACK_BOT_TOKEN="$SLACK_READ_USER_TOKEN"
   ```
   Optional local browser OAuth **only after** adding Redirect URL
   `http://127.0.0.1:8765/callback` on that app:
   ```bash
   ./examples/forge-real/oauth_slack_read.sh --localhost
   ```

3. Slack inventory:
   ```bash
   ./examples/forge-real/inventory_slack.sh
   ```

4. Seed mail + calendar:
   ```bash
   .eval-venv/bin/python examples/forge-real/seed_m365_graph.py
   ```

5. Sandbox smoke (7.2 image):
   ```bash
   ./examples/forge-real/smoke_sandbox.sh
   ```

6. Run cases:
   ```bash
   ./examples/run-openclaw-forge-agent-eval.sh
   ```

Image pin: `quay.io/aipcc/base-images/agentic/openclaw:latest` (OpenClaw **2026.7.2-beta.7**).
Do **not** use `quay.io/redhat-et/openclaw:csb-openclaw-only-openclaw-v2026.7.2-beta.7` —
OpenShell rejects it (`OCI USER '1001' resolves to prohibited primary GID 0`). Also avoid 8.1.
