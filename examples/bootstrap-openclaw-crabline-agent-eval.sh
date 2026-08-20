#!/usr/bin/env bash
# Create eval/openclaw-crabline-agent/ (eval.yaml + three agent cases) for the
# OpenShell + Quay OpenClaw → Crabline agent guide.
# Idempotent: overwrites the demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-crabline-agent"

mkdir -p \
  "${EVAL_DIR}/cases/case-001" \
  "${EVAL_DIR}/cases/case-002" \
  "${EVAL_DIR}/cases/case-003"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# OpenClaw agent (Quay) → host Crabline via exec+curl. AEH scores the recorder.
# Does NOT enable OpenClaw channels.slack (gateway segfault on Quay beta.7).
#
# Cases:
#   case-001 — smoke: open DM + post marker
#   case-002 — read history, post code from seed + marker
#   case-003 — threaded reply answering seeded 2+2 question
name: openclaw-crabline-agent

runner:
  type: openclaw
  # Prepended to the user prompt by openshell.run (OpenClaw rejects
  # agents.defaults.systemPrompt in openclaw-eval.json).
  system_prompt: |
    You are an evaluation agent. For this task you MUST use the exec tool to run
    curl against the Crabline Slack mock. Never simulate tool results. Never
    invent API responses. Prefer exec over describing shell commands in prose.
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

dataset:
  path: cases
  schema: |
    Each case has:
    - input.yaml: agent prompt (must not leak secrets for case-002/003)
    - annotations.yaml: expected_text / expected_code / expected_answer,
      slack_user (isolated DM per case), optional crabline_seed

outputs:
  - path: output
    schema: |
      response.txt: agent final response
      crabline-hits.jsonl: matching recorder lines (written by judges)
      crabline-seed.json: host seed metadata (channel/ts) when seeded

judges:
  - name: crabline_accepted_post
    module: agent_eval.openshell.crabline_score
    function: score_accepted_post_message
    feedback_type: bool

  - name: crabline_code_in_post
    if: "annotations.get('expected_code')"
    module: agent_eval.openshell.crabline_score
    function: score_post_with_code
    feedback_type: bool

  - name: crabline_threaded_answer
    if: "annotations.get('expect_thread_reply')"
    module: agent_eval.openshell.crabline_score
    function: score_threaded_answer
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
  used_exec_tool:
    min_pass_rate: 1.0
  response_received:
    min_pass_rate: 1.0
EOF

# --- case-001: smoke marker ---
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

# --- case-002: history + code ---
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

# --- case-003: threaded 2+2 ---
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

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001..003"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/case-00*/{input,annotations}.yaml
