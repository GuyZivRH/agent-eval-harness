# Slack auth for Forge real eval (local)

## What went wrong

`oauth_slack_read.sh` defaulted to:

```text
http://127.0.0.1:8765/callback
```

That URI is **not** registered on the Forge Slack Read Proxy app. Slack then returns
`redirect_uri did not match any configured URIs`.

## Two auth planes (do not confuse them)

| Material | What it is | Where it lives in SAW |
|---|---|---|
| `SLACK_READ_USER_TOKEN` / `user_token` (`xoxp-…`) | Real Slack **user** OAuth token for Web API | OpenShell provider credential → `runtimeEnv: SLACK_READ_USER_TOKEN: user_token` on the slack-read sandbox |
| `slack-drafts-bearer` | Inter-service bearer (drafts ↔ slack-write), **not** a Slack token | Secret `forge-writer-capabilities` → `/sandbox/persist/.forge-drafts/slack-drafts-bearer` |

The launcher clue:

```sh
DRAFTS_SENDPATH_BEARER="$(cat /sandbox/persist/.forge-drafts/slack-drafts-bearer)"
exec /sandbox/slack-send-service
```

only wires the **drafts** send-path bearer into `slack-send-service`. The Slack `xoxp`
token is injected separately by OpenShell from the `slack-read` / `slack-write`
provider credentials after onboarding (or after manual preauth).

## How SAW / forge-outlook-slack expects OAuth

From `onboarding.toml` (profile `forge-outlook-slack`):

```toml
# One entry callback for every provider grant; the front proxy relays the
# provider redirect here.
redirect_uri = "@@FORGE_ENTRY_ORIGIN@@/oauth/callback"
```

Resolved at deploy time to:

```text
https://<FORGE_ENTRY_HOST>/oauth/callback
```

Owner flow: browser → Forge entry `/connect` → Slack authorize → callback on the
**remote** entry origin → onboarding-service exchanges the code → stores
`user_token` on the gateway → slack-read/write sandboxes start once
`forge_initialized` is set.

That remote callback **cannot** be used by a local `127.0.0.1` listener. Local
eval does not run the Forge entry / onboarding host service.

## What rust-slack-proxy documents (local / milestone)

`slack-read-app-manifest.yaml` registers **no** `redirect_uris`. The supported
local path is:

1. https://api.slack.com/apps → **Forge Slack Read Proxy**
2. **OAuth & Permissions** → **Install to Workspace**
3. Copy **User OAuth Token** (`xoxp-…`) into `.tmp/forge-real/env`

## Non-secret IDs (from local env / profile)

| Field | Value |
|---|---|
| Team ID | `T0BS5PSMXDZ` |
| Read app client ID | `11889808745475.11928020233010` |
| Write app client ID | `11889808745475.11928063171794` |
| SAW redirect (template) | `{FORGE_ENTRY_ORIGIN}/oauth/callback` |
| Local helper redirect (opt-in only) | `http://127.0.0.1:8765/callback` |

## Next step for agent-eval-harness (usable read token)

**Preferred (works now, no Slack app edit):**

```bash
# 1) Slack UI → Forge Slack Read Proxy → OAuth & Permissions → Install to Workspace
# 2) Paste User OAuth Token:
printf '%s\n' "export SLACK_READ_USER_TOKEN='xoxp-...'" \
  "export SLACK_BOT_TOKEN=\"\$SLACK_READ_USER_TOKEN\"" \
  >> .tmp/forge-real/env
./examples/forge-real/inventory_slack.sh
```

**Optional local browser OAuth** (only after you add this exact Redirect URL on the
read app under OAuth & Permissions → Redirect URLs):

```text
http://127.0.0.1:8765/callback
```

Then:

```bash
./examples/forge-real/oauth_slack_read.sh --localhost
```

Do **not** point the local helper at `{FORGE_ENTRY_ORIGIN}/oauth/callback` unless
you have a live Forge entry that can receive the code — the helper cannot.
