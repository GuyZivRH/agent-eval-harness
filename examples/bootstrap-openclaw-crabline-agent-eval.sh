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
  "${EVAL_DIR}/cases/case-010" \
  "${EVAL_DIR}/cases/case-011" \
  "${EVAL_DIR}/cases/case-012" \
  "${EVAL_DIR}/cases/case-013"

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
#   case-010 — Gmail READ: list inbox, extract action-item code from email
#   case-011 — Gmail READ: search by sender, extract approval code
#   case-012 — Gmail READ: read message thread, extract decision code
#   case-013 — Gmail READ: list folders/labels and messages in folder, extract code
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
    if: "annotations.get('smolclaw_kind') == 'gmail' and not annotations.get('smolclaw_read_only')"
    module: agent_eval.openshell.smolclaw_score
    function: score_gmail_message_with_code
    feedback_type: bool

  - name: smolclaw_gmail_read
    if: "annotations.get('smolclaw_kind') == 'gmail' and annotations.get('smolclaw_read_only')"
    module: agent_eval.openshell.smolclaw_score
    function: score_gmail_read_extraction
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
  smolclaw_gmail_read:
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

# --- case-010: Gmail READ — inbox action item ---
cat > "${EVAL_DIR}/cases/case-010/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent mail or claim success without API JSON.

  Env already set: GMAIL_API_URL (Gmail API v1 mock on host).

  Task:
  1) GET ${GMAIL_API_URL}users/me/messages to list inbox messages
  2) For each message id returned, GET ${GMAIL_API_URL}users/me/messages/<id>?format=full
  3) Find the email about a sprint review / team update
  4) Extract the confirmation code from the email body (it follows "confirm with code")
  5) Final reply: include the exact confirmation code you found and briefly
     list the action items from the email

  Do not guess the code — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/case-010/annotations.yaml" <<'EOF'
expected_code: "GMAIL-READ-TASK-42"
smolclaw_kind: gmail
smolclaw_read_only: true
smolclaw_seed:
  kind: gmail
  from: "team-lead@example.com"
  subject: "Weekly Sprint Review - Action Items"
  body: >
    Hi team,

    Here are the action items from today's sprint review:
    1. Update the deployment scripts by Thursday (owner: DevOps)
    2. Review PR #142 for the auth refactor (owner: Backend)
    3. Confirm with code GMAIL-READ-TASK-42 that you received this email

    Please complete all items by end of week.

    Thanks,
    Team Lead
EOF

# --- case-011: Gmail READ — search by sender ---
cat > "${EVAL_DIR}/cases/case-011/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent mail or claim success without API JSON.

  Env already set: GMAIL_API_URL (Gmail API v1 mock on host).

  Task:
  1) GET ${GMAIL_API_URL}users/me/messages?q=from:ops-alert@example.com
     to search for emails from ops-alert@example.com
  2) GET the matching message with format=full to read its content
  3) Find the deployment approval code in the email body
  4) Final reply: include the exact approval code and the version number
     mentioned in the email

  Do not guess the code — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/case-011/annotations.yaml" <<'EOF'
expected_code: "URGENT-DEPLOY-KEY-88"
forbidden_codes:
  - "STAGING-DEPLOY-KEY-55"
smolclaw_kind: gmail
smolclaw_read_only: true
smolclaw_seed:
  kind: gmail
  messages:
    - from: "ci-bot@example.com"
      subject: "Staging deploy completed - STAGING-DEPLOY-KEY-55"
      body: >
        Staging deployment succeeded for v3.1.0-rc2.
        Staging verification code: STAGING-DEPLOY-KEY-55
        No action required — this is an automated notification.
    - from: "ops-alert@example.com"
      subject: "URGENT: Deployment approval needed for v3.2.1"
      body: >
        ALERT: Production deployment approval required.

        Release: v3.2.1
        Environment: production-us-east-1
        Approval code: URGENT-DEPLOY-KEY-88

        This deployment includes critical security patches.
        Please confirm receipt by including this approval code in your response.
EOF

# --- case-012: Gmail READ — thread summary ---
cat > "${EVAL_DIR}/cases/case-012/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent mail or claim success without API JSON.

  Env already set: GMAIL_API_URL (Gmail API v1 mock on host).

  Task:
  1) GET ${GMAIL_API_URL}users/me/messages to list inbox messages
  2) Look for messages about "Database migration plan"
  3) GET each related message with format=full to read the full conversation
     (or use GET ${GMAIL_API_URL}users/me/threads/<threadId> if you find a threadId)
  4) Summarize the thread: who participated, what was proposed, what was decided
  5) Find the final confirmation/decision code in the thread
  6) Final reply: include a brief summary of the discussion and the exact
     decision code from the thread

  Do not guess the content — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/case-012/annotations.yaml" <<'EOF'
expected_code: "DECISION-FINAL-77"
smolclaw_kind: gmail
smolclaw_read_only: true
smolclaw_seed:
  kind: gmail
  messages:
    - from: "alice@example.com"
      subject: "Database migration plan"
      body: >
        Team,

        I propose we migrate to PostgreSQL 16 for the analytics database.
        Key concern: downtime window must stay under 2 hours.
        Let me know your thoughts.

        — Alice
    - from: "bob@example.com"
      subject: "RE: Database migration plan"
      body: >
        Alice,

        Agreed on PostgreSQL 16. I can confirm a zero-downtime approach
        using logical replication is feasible.
        We should schedule for next Tuesday's maintenance window.

        — Bob
    - from: "carol@example.com"
      subject: "RE: Database migration plan"
      body: >
        All,

        Approved. Final decision: proceed with PostgreSQL 16 migration
        using logical replication during Tuesday's maintenance window.
        Confirmation code for this decision: DECISION-FINAL-77

        — Carol
EOF

# --- case-013: Gmail READ — list labels and read messages in folder ---
cat > "${EVAL_DIR}/cases/case-013/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT invent mail or claim success without API JSON.

  Env already set: GMAIL_API_URL (Gmail API v1 mock on host).

  Task:
  1) GET ${GMAIL_API_URL}users/me/labels to list all available labels/folders
  2) Look for messages in the "important" or "starred" label
  3) GET ${GMAIL_API_URL}users/me/messages?labelIds=<label_id> to find messages in that label
  4) Read the message in that label and extract the project tracking code
  5) Final reply: include the exact project tracking code and the folder name

  Do not guess the code — read it from messages in the labeled folder.
EOF
cat > "${EVAL_DIR}/cases/case-013/annotations.yaml" <<'EOF'
expected_code: "PROJECT-LABEL-7392"
smolclaw_kind: gmail
smolclaw_read_only: true
smolclaw_seed:
  kind: gmail
  subject: "Project tracking: Important milestone"
  body: >
    This is an important project tracking email.
    The project tracking code is: PROJECT-LABEL-7392

    Please mark this as important for future reference.
EOF

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001..005, case-010..013"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/case-*/{input,annotations}.yaml
