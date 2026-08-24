#!/usr/bin/env bash
# Create eval/openclaw-crabline-agent/ (eval.yaml + cases) for OpenShell +
# Quay OpenClaw → Crabline (Slack) + smolclaw (Gmail/Calendar).
# Idempotent: overwrites the demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-crabline-agent"

mkdir -p \
  "${EVAL_DIR}/cases/case-001" \
  "${EVAL_DIR}/cases/case-002" \
  "${EVAL_DIR}/cases/case-003" \
  "${EVAL_DIR}/cases/case-004" \
  "${EVAL_DIR}/cases/case-005" \
  "${EVAL_DIR}/cases/case-006" \
  "${EVAL_DIR}/cases/case-007" \
  "${EVAL_DIR}/cases/case-008" \
  "${EVAL_DIR}/cases/case-009" \
  "${EVAL_DIR}/cases/case-010"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# OpenClaw agent (Quay) → host mocks via exec+curl. AEH scores side effects.
# Slack: Crabline. Gmail/Calendar: smolclaw (bingran-you/smolclaw).
# Does NOT enable OpenClaw channels.slack (gateway segfault on Quay beta.7).
#
# Crabline Slack cases (001-008):
#   case-001 — chat:write: open DM + post marker
#   case-002 — im:read: list DMs, verify 3 seeded DMs found
#   case-003 — im:history: read DM history, extract seeded code
#   case-004 — threaded chat:write: read parent ts + post threaded reply
#   case-005 — channels:read: list public channels, verify 3 seeded channels found
#   case-006 — channels:history: read public channel history, extract seeded code
#   case-007 — groups:read: list private groups, verify 3 seeded groups found
#   case-008 — groups:history: read private group history, extract seeded code
#
# smolclaw cases (009-010):
#   case-009 — Calendar: find seeded code, create event with code + marker
#   case-010 — Gmail: find seeded code, send mail with code + marker
name: openclaw-crabline-agent

runner:
  type: openclaw
  system_prompt: |
    You are an evaluation agent. For this task you MUST use the exec tool to run
    curl against the host mock APIs configured in the environment (Slack via
    SLACK_API_URL, Gmail via GMAIL_API_URL, Calendar via CALENDAR_API_URL as
    present). Never simulate tool results. Never invent API responses. Prefer
    exec over describing shell commands in prose.
  providers:
    inference:
      baseUrl: https://inference.local/v1
      apiKey: empty
      models:
        - id: claude-sonnet-4
          name: Claude Sonnet 4
          api: openai-completions

execution:
  mode: case
  prompt: "{{ input.prompt }}"
  timeout: 300
  env:
    SLACK_API_URL: "http://host.openshell.internal:8787/api/"
    SLACK_BOT_TOKEN: "$SLACK_BOT_TOKEN"
    GMAIL_API_URL: "http://host.openshell.internal:8001/gmail/v1/"
    CALENDAR_API_URL: "http://host.openshell.internal:8002/calendar/v3/"

dataset:
  path: cases
  schema: |
    Each case has:
    - input.yaml: agent prompt (must not leak secrets for seeded cases)
    - annotations.yaml: expected_text / expected_code / expected_answer /
      expected_channels, slack_user, crabline_seed / crabline_seeds,
      or smolclaw_kind + smolclaw_seed

outputs:
  - path: output
    schema: |
      response.txt: agent final response
      crabline-hits.jsonl / smolclaw-*-hits.jsonl: matching side effects
      *-seed.json: host seed metadata when seeded

judges:
  - name: crabline_accepted_post
    if: "annotations.get('slack_user') and not annotations.get('read_only')"
    module: agent_eval.openshell.crabline_score
    function: score_accepted_post_message
    feedback_type: bool

  - name: crabline_threaded_answer
    if: "annotations.get('expect_thread_reply')"
    module: agent_eval.openshell.crabline_score
    function: score_threaded_answer
    feedback_type: bool

  - name: crabline_channels_listed
    if: "annotations.get('expected_channels')"
    module: agent_eval.openshell.crabline_score
    function: score_channels_listed
    feedback_type: bool

  - name: crabline_read_code
    if: "annotations.get('read_only') and annotations.get('expected_code')"
    module: agent_eval.openshell.crabline_score
    function: score_read_code_in_response
    feedback_type: bool

  - name: smolclaw_calendar_event
    if: "annotations.get('smolclaw_kind') == 'calendar'"
    module: agent_eval.openshell.smolclaw_score
    function: score_calendar_event_with_code
    feedback_type: bool

  - name: smolclaw_gmail_message
    if: "annotations.get('smolclaw_kind') == 'gmail'"
    module: agent_eval.openshell.smolclaw_score
    function: score_gmail_message_with_code
    feedback_type: bool

  - name: used_exec_tool
    check: |
      names = set()
      for t in outputs.get("tool_calls") or []:
        names.add(str(t.get("name") or t.get("tool") or "").lower())
      for ev in outputs.get("events") or []:
        for t in ev.get("tools") or []:
          names.add(str(t.get("name") or "").lower())
      return bool(names & {"exec", "bash", "shell", "terminal"})
    feedback_type: bool

  - name: response_received
    check: |
      response = outputs.get("output_content", "") or ""
      return len(response.strip()) > 0
    feedback_type: bool

thresholds:
  crabline_accepted_post:
    min_pass_rate: 1.0
  crabline_threaded_answer:
    min_pass_rate: 1.0
  crabline_channels_listed:
    min_pass_rate: 1.0
  crabline_read_code:
    min_pass_rate: 1.0
  smolclaw_calendar_event:
    min_pass_rate: 1.0
  smolclaw_gmail_message:
    min_pass_rate: 1.0
  used_exec_tool:
    min_pass_rate: 1.0
  response_received:
    min_pass_rate: 1.0
EOF

# =============================================================================
# case-001 — chat:write: open DM + post marker
# =============================================================================
cat > "${EVAL_DIR}/cases/case-001/input.yaml" <<'EOF'
prompt: |
  CRITICAL: You must call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel ids or claim success without curl JSON stdout.

  Goal: post a Slack message to the host Crabline mock API.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.
  This case uses Slack user UCASE001 only (isolated DM — do not use other users).

  1) conversations.open with users=UCASE001
  2) chat.postMessage with text exactly: aeh-crabline-agent-case-001-marker
  3) Final reply: one short line with channel id and ok=true
EOF
cat > "${EVAL_DIR}/cases/case-001/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-case-001-marker"
slack_user: UCASE001
EOF

# =============================================================================
# case-002 — im:read: list DMs, verify 3 seeded DMs found
# =============================================================================
cat > "${EVAL_DIR}/cases/case-002/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel lists.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.list with types=im — list all direct message channels
  2) Final reply: report every DM channel id and its associated user id
     from the API response. Include ok=true.

  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-002/annotations.yaml" <<'EOF'
read_only: true
expected_channels:
  - UCASE002A
  - UCASE002B
  - UCASE002C
crabline_seeds:
  - users: UCASE002A
    text: "DM alpha — project status update"
  - users: UCASE002B
    text: "DM beta — design review notes"
  - users: UCASE002C
    text: "DM gamma — deployment checklist"
EOF

# =============================================================================
# case-003 — im:history: read DM history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/case-003/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.history on channel=DCASE003
  2) Find the message that contains a confirmation code (format: XXXX-N)
  3) Final reply: the confirmation code you found and the full message text.
     Include ok=true.

  Do not guess the code — read it from channel history.
  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-003/annotations.yaml" <<'EOF'
expected_code: "ORANGE-7"
read_only: true
crabline_seed:
  channel: DCASE003
  text: >
    Action required: confirm with code ORANGE-7.
    Reply in this DM with that code.
EOF

# =============================================================================
# case-004 — threaded chat:write: read parent ts + threaded reply
# =============================================================================
cat > "${EVAL_DIR}/cases/case-004/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent Slack history, timestamps, or answers.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.
  This case uses Slack user UCASE004 only (isolated DM — do not open other users).

  Task:
  1) conversations.open with users=UCASE004
  2) conversations.history on that channel
  3) Find the parent question about arithmetic from "our last call"
  4) chat.postMessage a THREADED reply:
       - channel = that DM
       - thread_ts = the parent message ts
       - text must include the correct numeric answer AND marker
         aeh-crabline-agent-case-004-marker
  5) Final reply: one short line with thread_ts, answer, and ok=true

  The question text is only in this case's Slack history — read it. Do not skip thread_ts.
EOF
cat > "${EVAL_DIR}/cases/case-004/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-case-004-marker"
expected_answer: "4"
expect_thread_reply: true
slack_user: UCASE004
crabline_seed:
  users: UCASE004
  text: "Following our last call — what is 2+2? Please reply in this thread."
EOF

# =============================================================================
# case-005 — channels:read: list public channels, verify 3 seeded channels found
# =============================================================================
cat > "${EVAL_DIR}/cases/case-005/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel lists.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.list with types=public_channel — list all public channels
  2) Final reply: report every public channel id from the API response.
     Include ok=true.

  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-005/annotations.yaml" <<'EOF'
read_only: true
expected_channels:
  - CCASE005A
  - CCASE005B
  - CCASE005C
crabline_seeds:
  - channel: CCASE005A
    text: "Public channel alpha — general discussion"
  - channel: CCASE005B
    text: "Public channel beta — engineering updates"
  - channel: CCASE005C
    text: "Public channel gamma — team announcements"
EOF

# =============================================================================
# case-006 — channels:history: read public channel history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/case-006/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.history on channel=CCASE006
  2) Find the message that contains a confirmation code (format: CHAN-XXXX-N)
  3) Final reply: the confirmation code you found and the full message text.
     Include ok=true.

  Do not guess the code — read it from channel history.
  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-006/annotations.yaml" <<'EOF'
expected_code: "CHAN-VIOLET-3"
read_only: true
crabline_seed:
  channel: CCASE006
  text: >
    Weekly standup notes: deployment scheduled for Friday.
    Action required: confirm with code CHAN-VIOLET-3.
EOF

# =============================================================================
# case-007 — groups:read: list private groups, verify 3 seeded groups found
# =============================================================================
cat > "${EVAL_DIR}/cases/case-007/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel lists.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.list with types=private_channel — list all private groups
  2) Final reply: report every private group id from the API response.
     Include ok=true.

  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-007/annotations.yaml" <<'EOF'
read_only: true
expected_channels:
  - GCASE007A
  - GCASE007B
  - GCASE007C
crabline_seeds:
  - channel: GCASE007A
    text: "Private group alpha — security review"
  - channel: GCASE007B
    text: "Private group beta — incident response"
  - channel: GCASE007C
    text: "Private group gamma — compliance audit"
EOF

# =============================================================================
# case-008 — groups:history: read private group history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/case-008/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  1) conversations.history on channel=GCASE008
  2) Find the message that contains a confirmation code (format: GRP-XXXX-N)
  3) Final reply: the confirmation code you found and the full message text.
     Include ok=true.

  Do not guess the code — read it from group history.
  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/case-008/annotations.yaml" <<'EOF'
expected_code: "GRP-AMBER-6"
read_only: true
crabline_seed:
  channel: GCASE008
  text: >
    Security review findings: 2 items need follow-up.
    Action required: confirm with code GRP-AMBER-6.
EOF

# =============================================================================
# case-009 — Calendar (smolclaw)
# =============================================================================
cat > "${EVAL_DIR}/cases/case-009/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent calendar events or claim success without API JSON.

  Env already set: CALENDAR_API_URL (Google Calendar v3 mock on host).
  Optional hint: SMOLCLAW_SEED_EVENT_ID.

  Task:
  1) GET ${CALENDAR_API_URL}calendars/primary/events (optionally with q=)
  2) Find the seeded event whose description asks you to confirm with a code
  3) POST ${CALENDAR_API_URL}calendars/primary/events creating ONE new event whose
     summary AND description both include:
       - that confirmation code (exactly as written)
       - and the marker: aeh-smolclaw-case-009-marker
  4) Final reply: one short line with new event id, code, and ok=true

  Do not guess the code — read it from calendar events.
EOF
cat > "${EVAL_DIR}/cases/case-009/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-case-009-marker"
expected_code: "CALENDAR-BLUE-9"
smolclaw_kind: calendar
smolclaw_seed:
  kind: calendar
  calendar_id: primary
  summary: "AEH Seed Confirm CALENDAR-BLUE-9"
  description: >
    Action required: confirm with code CALENDAR-BLUE-9.
    Create a follow-up calendar event that includes that code and your eval marker.
EOF

# =============================================================================
# case-010 — Gmail (smolclaw)
# =============================================================================
cat > "${EVAL_DIR}/cases/case-010/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent mail or claim success without API JSON.

  Env already set: GMAIL_API_URL (Gmail API v1 mock on host).
  Optional hints: SMOLCLAW_SEED_MESSAGE_ID / SMOLCLAW_SEED_THREAD_ID.

  Task:
  1) GET ${GMAIL_API_URL}users/me/messages?q=… to find the seeded action email
  2) GET the message (format=full) and read the confirmation code from subject/body
  3) POST ${GMAIL_API_URL}users/me/messages/send with a raw RFC822 message whose
     Subject and body both include:
       - that confirmation code (exactly as written)
       - and the marker: aeh-smolclaw-case-010-marker
     (base64url-encode the raw message for the JSON "raw" field)
  4) Final reply: one short line with message id, code, and ok=true

  Do not guess the code — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/case-010/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-case-010-marker"
expected_code: "MAIL-ORANGE-7"
smolclaw_kind: gmail
smolclaw_seed:
  kind: gmail
  subject: "Action required confirm with code MAIL-ORANGE-7"
  body: >
    Please confirm with code MAIL-ORANGE-7.
    Reply (send) an email that includes that code and your eval marker.
EOF

# Remove stale cases from previous bootstrap runs
for old in "${EVAL_DIR}/cases/case-0"*; do
  n="$(basename "$old")"
  case "$n" in
    case-001|case-002|case-003|case-004|case-005|\
    case-006|case-007|case-008|case-009|case-010) ;;
    *) rm -rf "$old" ;;
  esac
done

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001..010"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/case-0*/{input,annotations}.yaml
