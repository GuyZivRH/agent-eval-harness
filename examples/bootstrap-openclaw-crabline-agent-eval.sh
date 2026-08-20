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
  "${EVAL_DIR}/cases/case-005"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# OpenClaw agent (Quay) → host mocks via exec+curl. AEH scores side effects.
# Slack: Crabline. Gmail/Calendar: smolclaw (bingran-you/smolclaw).
# Does NOT enable OpenClaw channels.slack (gateway segfault on Quay beta.7).
#
# Cases:
#   case-001 — Slack smoke: open DM + post marker
#   case-002 — Slack: read history, post code from seed + marker
#   case-003 — Slack: threaded reply answering seeded 2+2
#   case-004 — Calendar: find seeded code, create event with code + marker
#   case-005 — Gmail: find seeded code, send mail with code + marker
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
    - annotations.yaml: expected_text / expected_code / expected_answer,
      slack_user and optional crabline_seed, or smolclaw_kind + smolclaw_seed

outputs:
  - path: output
    schema: |
      response.txt: agent final response
      crabline-hits.jsonl / smolclaw-*-hits.jsonl: matching side effects
      *-seed.json: host seed metadata when seeded

judges:
  - name: crabline_accepted_post
    if: "annotations.get('slack_user')"
    module: agent_eval.openshell.crabline_score
    function: score_accepted_post_message
    feedback_type: bool

  - name: crabline_code_in_post
    if: "annotations.get('expected_code') and annotations.get('slack_user')"
    module: agent_eval.openshell.crabline_score
    function: score_post_with_code
    feedback_type: bool

  - name: crabline_threaded_answer
    if: "annotations.get('expect_thread_reply')"
    module: agent_eval.openshell.crabline_score
    function: score_threaded_answer
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
  crabline_code_in_post:
    min_pass_rate: 1.0
  crabline_threaded_answer:
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

# --- case-001: Slack smoke ---
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

# --- case-002: Slack history + code ---
cat > "${EVAL_DIR}/cases/case-002/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent Slack history or message text.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.
  Optional hints: CRABLINE_SEED_CHANNEL / CRABLINE_SEED_TS.
  This case uses Slack user UCASE002 only (isolated DM — do not open other users).

  Task:
  1) conversations.open with users=UCASE002
  2) conversations.history on that channel
  3) Find the action message that asks you to confirm with a code
  4) chat.postMessage with ONE message that includes:
       - the confirmation code from history (exactly as written there)
       - and the marker: aeh-crabline-agent-case-002-marker
  5) Final reply: one short line with the code you posted and ok=true

  Do not guess the code. It must come from conversations.history for UCASE002.
EOF
cat > "${EVAL_DIR}/cases/case-002/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-case-002-marker"
expected_code: "ORANGE-7"
slack_user: UCASE002
crabline_seed:
  users: UCASE002
  text: >
    Action required: confirm with code ORANGE-7.
    Reply in this DM with that code (and your eval marker).
EOF

# --- case-003: Slack thread ---
cat > "${EVAL_DIR}/cases/case-003/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent Slack history, timestamps, or answers.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.
  Optional hints: CRABLINE_SEED_CHANNEL / CRABLINE_SEED_TS.
  This case uses Slack user UCASE003 only (isolated DM — do not open other users).

  Task:
  1) conversations.open with users=UCASE003
  2) conversations.history on that channel (or use CRABLINE_SEED_TS)
  3) Find the parent question about arithmetic from "our last call"
  4) chat.postMessage a THREADED reply:
       - channel = that DM
       - thread_ts = the parent message ts
       - text must include the correct numeric answer AND marker
         aeh-crabline-agent-case-003-marker
  5) Final reply: one short line with thread_ts, answer, and ok=true

  The question text is only in this case's Slack history — read it. Do not skip thread_ts.
EOF
cat > "${EVAL_DIR}/cases/case-003/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-case-003-marker"
expected_answer: "4"
expect_thread_reply: true
slack_user: UCASE003
crabline_seed:
  users: UCASE003
  text: "Following our last call — what is 2+2? Please reply in this thread."
EOF

# --- case-004: Calendar ---
cat > "${EVAL_DIR}/cases/case-004/input.yaml" <<'EOF'
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
       - and the marker: aeh-smolclaw-case-004-marker
  4) Final reply: one short line with new event id, code, and ok=true

  Do not guess the code — read it from calendar events.
EOF
cat > "${EVAL_DIR}/cases/case-004/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-case-004-marker"
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

# --- case-005: Gmail ---
cat > "${EVAL_DIR}/cases/case-005/input.yaml" <<'EOF'
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
       - and the marker: aeh-smolclaw-case-005-marker
     (base64url-encode the raw message for the JSON "raw" field)
  4) Final reply: one short line with message id, code, and ok=true

  Do not guess the code — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/case-005/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-case-005-marker"
expected_code: "MAIL-ORANGE-7"
smolclaw_kind: gmail
smolclaw_seed:
  kind: gmail
  subject: "Action required confirm with code MAIL-ORANGE-7"
  body: >
    Please confirm with code MAIL-ORANGE-7.
    Reply (send) an email that includes that code and your eval marker.
EOF

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001..005"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/case-00*/{input,annotations}.yaml
