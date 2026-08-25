#!/usr/bin/env bash
# Create eval/openclaw-crabline-agent/ (eval.yaml + cases) for OpenShell +
# Quay OpenClaw → Crabline (Slack) + smolclaw (Gmail/Calendar).
# Idempotent: overwrites the demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-crabline-agent"

mkdir -p \
  "${EVAL_DIR}/cases/chat-write" \
  "${EVAL_DIR}/cases/im-read" \
  "${EVAL_DIR}/cases/im-history" \
  "${EVAL_DIR}/cases/chat-write-threaded" \
  "${EVAL_DIR}/cases/channels-read" \
  "${EVAL_DIR}/cases/channels-history" \
  "${EVAL_DIR}/cases/groups-read" \
  "${EVAL_DIR}/cases/groups-history" \
  "${EVAL_DIR}/cases/smolclaw-calendar" \
  "${EVAL_DIR}/cases/smolclaw-gmail" \
  "${EVAL_DIR}/cases/digest-overnight" \
  "${EVAL_DIR}/cases/draft-reply" \
  "${EVAL_DIR}/cases/meeting-prep"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# OpenClaw agent (Quay) → host mocks via exec+curl. AEH scores side effects.
# Slack: Crabline. Gmail/Calendar: smolclaw (bingran-you/smolclaw).
# Does NOT enable OpenClaw channels.slack (gateway segfault on Quay beta.7).
#
# Crabline Slack cases (001-008):
#   chat-write — chat:write: open DM + post marker
#   im-read — im:read: list DMs, verify 3 seeded DMs found
#   im-history — im:history: read DM history, extract seeded code
#   chat-write-threaded — threaded chat:write: read parent ts + post threaded reply
#   channels-read — channels:read: list public channels, verify 3 seeded channels found
#   channels-history — channels:history: read public channel history, extract seeded code
#   groups-read — groups:read: list private groups, verify 3 seeded groups found
#   groups-history — groups:history: read private group history, extract seeded code
#
# smolclaw cases (009-010):
#   smolclaw-calendar — Calendar: find seeded code, create event with code + marker
#   smolclaw-gmail — Gmail: find seeded code, send mail with code + marker
#
# Slack summarization cases (011-013) — LLM judge (Opus):
#   digest-overnight — Overnight digest: read channels/DMs, prioritize urgent vs low
#   draft-reply — Draft reply: read DM, draft a suggested response (read-only)
#   meeting-prep — Meeting prep: read threads, produce prep doc, exclude irrelevant
name: openclaw-crabline-agent

models:
  judge: claude-haiku-4-5

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

  - name: digest_quality
    if: "annotations.get('digest_evaluation')"
    prompt: |
      You are evaluating a Slack overnight digest produced by an agent.

      The following messages were seeded into Slack (this is the ground truth):
      {% for msg in annotations.seeded_messages %}
      - [{{ msg.urgency }}] ({{ msg.channel_type }}): "{{ msg.text }}"
      {% endfor %}

      Messages that MUST be flagged as urgent/high-priority:
      {% for item in annotations.expected_urgent %}
      - {{ item }}
      {% endfor %}

      Messages that MUST be flagged as low-priority/can-wait:
      {% for item in annotations.expected_low %}
      - {{ item }}
      {% endfor %}

      Agent's digest:
      {{ outputs.output_content }}

      Evaluate:
      1. Are ALL seeded messages accounted for in the digest?
      2. Does each item include a channel or DM ID (e.g. CDIGEST011, GDIGEST011, D...)?
      3. Does each item include the message timestamp?
      4. Does each item include a brief summary that captures the key point?
      5. Are the urgent items correctly identified as urgent/high-priority?
      6. Are the low-priority items correctly identified as low/can-wait?
      7. Does the digest avoid adding action recommendations that weren't in the original messages?

      Return true ONLY if all messages are present with channel IDs, timestamps,
      and summaries, and urgency classification is correct.
    feedback_type: bool

  - name: draft_reply_quality
    if: "annotations.get('draft_evaluation')"
    prompt: |
      You are evaluating a draft Slack reply produced by an agent.

      The agent was asked to draft a response to this message:
      "{{ annotations.original_message }}"

      Key points the draft MUST address:
      {% for point in annotations.expected_points %}
      - {{ point }}
      {% endfor %}

      Agent's draft reply:
      {{ outputs.output_content }}

      Evaluate:
      1. Does the draft address the original message's question/request?
      2. Does the draft cover all expected key points?
      3. Is the draft professional and appropriate in tone?
      4. Does the draft avoid making up information that wasn't in the original message?

      Return true if the draft is relevant, covers the key points, and does not fabricate details.
    feedback_type: bool

  - name: meeting_prep_quality
    if: "annotations.get('meeting_prep_evaluation')"
    prompt: |
      You are evaluating a meeting prep document produced by an agent.

      The agent was given a calendar event:
        Title: "Leadership Sync — Q3 Review"
        Agenda: Q3 hiring plan update, Vendor contract renewal, Product roadmap review

      Relevant Slack content that MUST appear in the prep doc:
      {% for topic in annotations.expected_topics %}
      - {{ topic }}
      {% endfor %}

      Irrelevant Slack content that MUST NOT appear in the prep doc:
      {% for topic in annotations.excluded_topics %}
      - {{ topic }}
      {% endfor %}

      Agent's meeting prep document:
      {{ outputs.output_content }}

      Evaluate:
      1. Does the prep doc reference the meeting title or agenda from the calendar?
      2. Are all expected Slack topics covered in the prep doc?
      3. Are excluded topics completely absent from the prep doc — not mentioned
         at all, not even to say they are irrelevant or not related?
      4. Are all agenda topics from the calendar listed in the prep doc, even those
         without additional Slack context?
      5. Does the prep doc avoid filler like "no context found" or "nothing relevant"
         for agenda topics without Slack data?
      6. Is the document organized as a useful meeting preparation?

      Return true ONLY if the prep doc references the calendar agenda, all expected
      topics are present, all agenda items listed, excluded topics are completely
      absent (not mentioned in any form), and there are no filler sections.
    feedback_type: bool

  - name: no_message_posted
    if: "annotations.get('draft_evaluation')"
    check: |
      from pathlib import Path
      import json, os
      recorder = Path(os.environ.get("CRABLINE_RECORDER", "")).expanduser()
      if not recorder.is_file():
        return True
      ann = outputs.get("annotations") or {}
      seed_text = (ann.get("original_message") or "").strip()[:80]
      for line in recorder.read_text().splitlines():
        if not line.strip():
          continue
        try:
          event = json.loads(line)
        except Exception:
          continue
        if event.get("path") == "/api/chat.postMessage" and event.get("accepted"):
          body = event.get("body") or {}
          channel = body.get("channel", "")
          text = str(body.get("text", ""))
          if channel == "DDRAFT012" and seed_text and seed_text not in text:
            return False, "agent posted to DDRAFT012 — should be draft only"
      return True, "no agent postMessage to DDRAFT012 (seed post excluded)"
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
  digest_quality:
    min_pass_rate: 1.0
  draft_reply_quality:
    min_pass_rate: 1.0
  no_message_posted:
    min_pass_rate: 1.0
  meeting_prep_quality:
    min_pass_rate: 1.0
  used_exec_tool:
    min_pass_rate: 1.0
  response_received:
    min_pass_rate: 1.0
EOF

# =============================================================================
# chat-write — chat:write: open DM + post marker
# =============================================================================
cat > "${EVAL_DIR}/cases/chat-write/input.yaml" <<'EOF'
prompt: |
  CRITICAL: You must call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel ids or claim success without curl JSON stdout.

  Goal: post a Slack message to the host Crabline mock API.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.
  This case uses Slack user UCASE001 only (isolated DM — do not use other users).

  1) conversations.open with users=UCASE001
  2) chat.postMessage with text exactly: aeh-crabline-agent-chat-write-marker
  3) Final reply: one short line with channel id and ok=true
EOF
cat > "${EVAL_DIR}/cases/chat-write/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-chat-write-marker"
slack_user: UCASE001
EOF

# =============================================================================
# im-read — im:read: list DMs, verify 3 seeded DMs found
# =============================================================================
cat > "${EVAL_DIR}/cases/im-read/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/im-read/annotations.yaml" <<'EOF'
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
# im-history — im:history: read DM history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/im-history/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/im-history/annotations.yaml" <<'EOF'
expected_code: "ORANGE-7"
read_only: true
crabline_seed:
  channel: DCASE003
  text: >
    Action required: confirm with code ORANGE-7.
    Reply in this DM with that code.
EOF

# =============================================================================
# chat-write-threaded — threaded chat:write: read parent ts + threaded reply
# =============================================================================
cat > "${EVAL_DIR}/cases/chat-write-threaded/input.yaml" <<'EOF'
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
         aeh-crabline-agent-chat-write-threaded-marker
  5) Final reply: one short line with thread_ts, answer, and ok=true

  The question text is only in this case's Slack history — read it. Do not skip thread_ts.
EOF
cat > "${EVAL_DIR}/cases/chat-write-threaded/annotations.yaml" <<'EOF'
expected_text: "aeh-crabline-agent-chat-write-threaded-marker"
expected_answer: "4"
expect_thread_reply: true
slack_user: UCASE004
crabline_seed:
  users: UCASE004
  text: "Following our last call — what is 2+2? Please reply in this thread."
EOF

# =============================================================================
# channels-read — channels:read: list public channels, verify 3 seeded channels found
# =============================================================================
cat > "${EVAL_DIR}/cases/channels-read/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/channels-read/annotations.yaml" <<'EOF'
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
# channels-history — channels:history: read public channel history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/channels-history/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/channels-history/annotations.yaml" <<'EOF'
expected_code: "CHAN-VIOLET-3"
read_only: true
crabline_seed:
  channel: CCASE006
  text: >
    Weekly standup notes: deployment scheduled for Friday.
    Action required: confirm with code CHAN-VIOLET-3.
EOF

# =============================================================================
# groups-read — groups:read: list private groups, verify 3 seeded groups found
# =============================================================================
cat > "${EVAL_DIR}/cases/groups-read/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/groups-read/annotations.yaml" <<'EOF'
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
# groups-history — groups:history: read private group history, extract seeded code
# =============================================================================
cat > "${EVAL_DIR}/cases/groups-history/input.yaml" <<'EOF'
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
cat > "${EVAL_DIR}/cases/groups-history/annotations.yaml" <<'EOF'
expected_code: "GRP-AMBER-6"
read_only: true
crabline_seed:
  channel: GCASE008
  text: >
    Security review findings: 2 items need follow-up.
    Action required: confirm with code GRP-AMBER-6.
EOF

# =============================================================================
# smolclaw-calendar — Calendar (smolclaw)
# =============================================================================
cat > "${EVAL_DIR}/cases/smolclaw-calendar/input.yaml" <<'EOF'
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
       - and the marker: aeh-smolclaw-smolclaw-calendar-marker
  4) Final reply: one short line with new event id, code, and ok=true

  Do not guess the code — read it from calendar events.
EOF
cat > "${EVAL_DIR}/cases/smolclaw-calendar/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-smolclaw-calendar-marker"
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
# smolclaw-gmail — Gmail (smolclaw)
# =============================================================================
cat > "${EVAL_DIR}/cases/smolclaw-gmail/input.yaml" <<'EOF'
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
       - and the marker: aeh-smolclaw-smolclaw-gmail-marker
     (base64url-encode the raw message for the JSON "raw" field)
  4) Final reply: one short line with message id, code, and ok=true

  Do not guess the code — read it from Gmail.
EOF
cat > "${EVAL_DIR}/cases/smolclaw-gmail/annotations.yaml" <<'EOF'
expected_text: "aeh-smolclaw-smolclaw-gmail-marker"
expected_code: "MAIL-ORANGE-7"
smolclaw_kind: gmail
smolclaw_seed:
  kind: gmail
  subject: "Action required confirm with code MAIL-ORANGE-7"
  body: >
    Please confirm with code MAIL-ORANGE-7.
    Reply (send) an email that includes that code and your eval marker.
EOF

# =============================================================================
# digest-overnight — Overnight Slack Digest (LLM judge)
# =============================================================================
cat > "${EVAL_DIR}/cases/digest-overnight/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel lists or message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN, CRABLINE_SEED_OLDEST.

  Task (READ ONLY — do not post any messages):
  You are producing an overnight Slack digest for a manager arriving in the morning.

  1) conversations.list with types=public_channel,private_channel,im — discover
     all channels, groups, and DMs
  2) conversations.history on each channel with oldest=$CRABLINE_SEED_OLDEST
     to read only recent messages (skip older history)
  3) Produce a prioritized digest with two sections:
     - URGENT / needs attention: items requiring immediate action
     - LOW PRIORITY / can wait: informational items
  4) For each item in the digest:
     - The channel id from the conversations.list response where the message was found
     - The message timestamp
     - A brief summary of the message content
  5) Final reply: the full digest with every message summarized and categorized.
     Do not add action recommendations — just summarize and prioritize.

  Do not guess message content — read it from Slack.
  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/digest-overnight/annotations.yaml" <<'EOF'
read_only: true
digest_evaluation: true
seeded_messages:
  - channel_type: public
    urgency: urgent
    text: "Server alert: production DB connection pool exhausted since 3am. On-call team investigating."
  - channel_type: public
    urgency: low
    text: "Team lunch moved from Wednesday to Thursday this week."
  - channel_type: private
    urgency: urgent
    text: "Need budget approval for Q4 contractor hire by end of day. Please review the attached proposal."
  - channel_type: dm
    urgency: low
    text: "Can you review my PR #247 when you get a chance? No rush."
  - channel_type: dm
    urgency: low
    text: "Happy birthday! Hope you have a great day."
expected_urgent:
  - "production DB connection pool"
  - "budget approval"
expected_low:
  - "lunch"
  - "PR #247"
  - "birthday"
crabline_seeds:
  - channel: CDIGEST011
    text: "Server alert: production DB connection pool exhausted since 3am. On-call team investigating."
  - channel: CDIGEST011
    text: "Team lunch moved from Wednesday to Thursday this week."
  - channel: GDIGEST011
    text: "Need budget approval for Q4 contractor hire by end of day. Please review the attached proposal."
  - users: UDIGEST011A
    text: "Can you review my PR #247 when you get a chance? No rush."
  - users: UDIGEST011B
    text: "Happy birthday! Hope you have a great day."
EOF

# =============================================================================
# draft-reply — Draft Slack Reply / DM (LLM judge)
# =============================================================================
cat > "${EVAL_DIR}/cases/draft-reply/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN.

  Task (READ ONLY — do not post any messages):
  You are drafting a suggested reply to a Slack DM on behalf of a manager.

  1) conversations.history on channel=DDRAFT012
  2) Read the message that needs a response
  3) Draft a short, professional reply that addresses the sender's question
  4) Final reply: the suggested response text. Do NOT post it to Slack.

  Do not guess message content — read it from Slack.
  Do not make up information — only respond to what was asked.
  Do not call chat.postMessage — this is a draft, not a posted message.
EOF
cat > "${EVAL_DIR}/cases/draft-reply/annotations.yaml" <<'EOF'
read_only: true
draft_evaluation: true
original_message: >
  Hey, are you coming to the team offsite next week?
  We need a headcount for the restaurant booking by Wednesday.
expected_points:
  - "offsite attendance"
  - "headcount deadline"
crabline_seed:
  channel: DDRAFT012
  text: >
    Hey, are you coming to the team offsite next week?
    We need a headcount for the restaurant booking by Wednesday.
EOF

# =============================================================================
# meeting-prep — Meeting Prep from Slack threads (LLM judge)
# =============================================================================
cat > "${EVAL_DIR}/cases/meeting-prep/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent channel lists or message history.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN, CALENDAR_API_URL.

  Task (READ ONLY — do not post any messages):
  You are preparing a one-page meeting prep document for the "Leadership Sync"
  meeting by pulling relevant context from Slack channels.

  1) GET ${CALENDAR_API_URL}calendars/primary/events to find the "Leadership Sync" meeting
  2) Read its event details to learn the agenda topics
  3) conversations.list with types=public_channel — discover Slack channels
  4) conversations.history on each channel to read messages
  5) Include only messages relevant to the meeting agenda topics
  6) Completely exclude messages unrelated to the agenda — do not mention them
     at all, not even to say they are irrelevant
  7) If an agenda topic has no relevant Slack context, still include it as an
     agenda item but do not add filler like "no context found" or "nothing relevant"
  8) Final reply: produce a structured meeting prep document with:
     - The meeting title and agenda (from the calendar event)
     - Relevant Slack context and decisions for each agenda topic
     - Action items or deadlines mentioned

  Do not guess content — read it from the calendar and Slack.
  Do not call chat.postMessage — this is a read-only task.
EOF
cat > "${EVAL_DIR}/cases/meeting-prep/annotations.yaml" <<'EOF'
read_only: true
meeting_prep_evaluation: true
expected_topics:
  - "Q3 hiring plan"
  - "3 approved headcount for backend engineering"
  - "Vendor B"
  - "$45K/year"
  - "product roadmap review"
excluded_topics:
  - "snack preferences"
  - "vending machine"
  - "trail mix"
smolclaw_seed:
  kind: calendar
  calendar_id: primary
  summary: "Leadership Sync — Q3 Review"
  description: >
    Agenda:
    1) Q3 hiring plan update
    2) Vendor contract renewal
    3) Product roadmap review
crabline_seeds:
  - channel: CPREP013A
    text: >
      Leadership sync agenda for Monday:
      1) Q3 hiring plan update
      2) Vendor contract renewal (deadline Sept 1)
      3) Product roadmap review
  - channel: CPREP013A
    text: >
      Update on hiring: we have 3 approved headcount for backend engineering.
      Interviews start next week. Recruiter has 12 candidates in pipeline.
  - channel: CPREP013A
    text: >
      Vendor contract decision: proceeding with Vendor B at $45K/year.
      Legal review complete. Signing scheduled for Thursday.
  - channel: CPREP013B
    text: >
      Office snack preferences poll: please vote for new vending machine
      options by Friday. Current budget is $200/month.
  - channel: CPREP013B
    text: >
      Current favorites: trail mix (12 votes), sparkling water (8 votes),
      protein bars (6 votes). Will order top 3 next week.
EOF

# Remove stale cases from previous bootstrap runs
for old in "${EVAL_DIR}/cases/"*; do
  [ -d "$old" ] || continue
  n="$(basename "$old")"
  case "$n" in
    chat-write|im-read|im-history|chat-write-threaded|channels-read|\
    channels-history|groups-read|groups-history|smolclaw-calendar|smolclaw-gmail|\
    digest-overnight|draft-reply|meeting-prep) ;;
    *) rm -rf "$old" ;;
  esac
done

echo "Wrote ${EVAL_DIR}/eval.yaml and 13 cases"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/*/{input,annotations}.yaml
