#!/usr/bin/env bash
# Create eval/openclaw-forge-cos-agent/ for COS agent evaluation.
# Installs the chief-of-staff CLAW package via slash command invocation.
# Two cases: /daily-briefing (existing skill) and /analysis (future skill).
# Idempotent: overwrites demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-forge-cos-agent"

mkdir -p \
  "${EVAL_DIR}/scenes" \
  "${EVAL_DIR}/cases/daily-briefing" \
  "${EVAL_DIR}/cases/analysis-panel"

# Verify forge-agent-catalog is cloned into examples/
# Clone: git clone git@github.com:rh-forge/forge-agent-catalog.git resources/forge-agent-catalog
COS_PKG="${ROOT}/resources/forge-agent-catalog/chief-of-staff"
if [[ ! -d "${COS_PKG}" ]]; then
  echo "error: ${COS_PKG} not found" >&2
  echo "Clone the forge-agent-catalog repo:" >&2
  echo "  git clone git@github.com:rh-forge/forge-agent-catalog.git resources/forge-agent-catalog" >&2
  exit 1
fi
echo "Using chief-of-staff package at ${COS_PKG} ($(find "${COS_PKG}" -type f | wc -l) files)"

# --- eval.yaml ---
# No system_prompt needed — agent --local loads identity from installed workspace.
cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# COS agent evaluation — Chief of Staff daily briefing via slash commands
# COS agent installed via openclaw claws add, run via agent --agent --local
# Identity loaded automatically from installed workspace (SOUL.md, AGENTS.md)
name: forge-cos-agent

scene: monday-acquisition

models:
  judge: claude-sonnet-4-5@20250929

runner:
  type: openclaw
  providers:
    inference:
      baseUrl: https://inference.local/v1
      apiKey: empty
      models:
        - id: claude-opus-4-6
          name: Claude Opus 4.6
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
    FORGE_UI_BASE_URL: "http://localhost:9999"
    FORGE_AGENT_TOKEN: "eval-dummy-token"
    INTERVM_BEARER: "eval-dummy-bearer"

dataset:
  path: cases
  schema: |
    Each case has:
    - input.yaml: slash command prompt to invoke a COS skill
    - annotations.yaml: ground truth expectations for LLM rubric judges

outputs:
  - path: output
    schema: |
      response.txt: agent final response

judges:
  # --- Prioritization judges (daily-briefing case) ---
  - name: prioritization_recall
    if: "annotations.get('expected_top_of_mind')"
    prompt: |
      You are evaluating a daily briefing produced by a Chief of Staff agent.

      The briefing should surface all items that require the executive's action
      today in the "Top of Mind" section.

      Items that MUST appear in Top of Mind:
      {% for item in annotations.expected_top_of_mind %}
      - {{ item }}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score RECALL on a 1-5 scale:
      5: Every item on the expected list is present in Top of Mind. None missing.
      4: Every expected item is somewhere the executive would see it this morning,
         but one landed in the wrong section.
      3: One expected item is missing from Top of Mind or buried below the fold.
      2: Two expected items are missing, or the single most critical item is missing.
      1: Something the executive cannot afford to miss is not on the briefing.
    score_range: [1, 5]
    feedback_type: int

  - name: prioritization_precision
    if: "annotations.get('expected_excluded')"
    prompt: |
      You are evaluating a daily briefing produced by a Chief of Staff agent.

      The briefing should NOT include noise in the "Top of Mind" section.

      Items that MUST be excluded from Top of Mind:
      {% for item in annotations.expected_excluded %}
      - {{ item }}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score PRECISION on a 1-5 scale:
      5: Every card in Top of Mind is something the executive should handle today.
      4: One card is a close call but still a real work item.
      3: One or two cards should not be there (bot, newsletter, social).
      2: Several cards are filler, or ranking hid a time-critical ask.
      1: Top of Mind is mostly filler or empty despite real work in the inbox.
    score_range: [1, 5]
    feedback_type: int

  - name: prioritization_relevance
    if: "annotations.get('expected_first')"
    prompt: |
      You are evaluating a daily briefing produced by a Chief of Staff agent.

      The highest-stakes item that should be first:
      {{ annotations.expected_first }}

      Agent's briefing:
      {{ outputs.output_content }}

      Score RELEVANCE on a 1-5 scale:
      5: The top card is the highest-stakes item with specific, true reasoning.
      4: Two neighboring cards are swapped but highest-stakes is still first.
      3: Right items but wrong order — newest or loudest beat the actual decision.
      2: A low-stakes item sits above something that needs a decision.
      1: No ranking or the list is so filtered the real day is hidden.
    score_range: [1, 5]
    feedback_type: int

  # --- Analysis judges (analysis-panel case) ---
  - name: analysis_accuracy
    if: "annotations.get('expected_facts')"
    prompt: |
      You are evaluating an analysis panel produced by a Chief of Staff agent.

      Facts that MUST appear accurately:
      {% for fact in annotations.expected_facts %}
      - {{ fact }}
      {% endfor %}

      Agent's analysis:
      {{ outputs.output_content }}

      Score ACCURACY on a 1-5 scale:
      5: Names, dates, numbers match sources. No fabricated facts.
      4: Decision-changing facts are right. One small miss that wouldn't change the call.
      3: Nothing made up but a constraint that should affect caution is missing.
      2: One fact is wrong, or a missing line would have changed how careful they should be.
      1: Invented deadline, number, or source.
    score_range: [1, 5]
    feedback_type: int

  - name: analysis_independent_judgment
    if: "annotations.get('expected_synthesis')"
    prompt: |
      You are evaluating whether the analysis adds independent value beyond
      paraphrasing the source messages.

      Expected synthesis:
      {% for item in annotations.expected_synthesis %}
      - {{ item }}
      {% endfor %}

      Agent's analysis:
      {{ outputs.output_content }}

      Score INDEPENDENT JUDGMENT on a 1-5 scale:
      5: Analysis does work the sender did not — stakes, verification needs, recommendation.
      4: Real recommendation but thin verification list.
      3: Competent summary with weak generic recommendation.
      2: Mostly repeats what the sender wrote.
      1: Opening the analysis does not help decide.
    score_range: [1, 5]
    feedback_type: int

  # --- Generic judges (both cases) ---
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
  prioritization_recall:
    min_mean: 5.0
  prioritization_precision:
    min_mean: 5.0
  prioritization_relevance:
    min_mean: 5.0
  analysis_accuracy:
    min_mean: 5.0
  analysis_independent_judgment:
    min_mean: 5.0
  used_exec_tool:
    min_pass_rate: 1.0
  response_received:
    min_pass_rate: 1.0
EOF

# --- Scene: monday-acquisition.yaml ---
cat > "${EVAL_DIR}/scenes/monday-acquisition.yaml" <<'EOF'
# Executive inbox scene — acquisition decision deadline
# Synthetic scenario for testing LLM-based prioritization, bundling, and synthesis

crabline_seeds:
  - channel: CACQUISITION
    user: Sarah Jenkins
    text: "DOJ concession terms arrived Friday — three items before we can close. I've sent my full analysis by email."
  - channel: CACQUISITION
    user: Marcus Thorne
    text: "Flagging: Nordic divestiture has a $240M write-down risk that wasn't in the original model. Need to discuss before sign-off."
  - channel: CACQUISITION
    user: Sarah Jenkins
    text: "One more thing — do not commit until APAC counsel reviews the final IP transfer clause. They're reviewing now, ETA 11am."
  - channel: CGENERAL
    user: Elena Rostova
    text: "APAC data center migration completed successfully overnight with zero downtime. CIO confirms we are fully cut over."
  - channel: CGENERAL
    user: Marcus Thorne
    text: "Competitor AeroGlobal unexpectedly missed earnings by 14% this morning, citing the same chip shortage we bypassed in Q2."
  - channel: COFFICE
    user: Elena Rostova
    text: "Team offsite venue options for October — please vote in the thread by Friday. Current top picks: lakeside retreat or downtown conference center."
  - channel: CGENERAL
    user: HR Bot
    text: "Happy birthday to Elena Rostova! Join us for cake in the break room at 3pm today."
  - channel: CGENERAL
    user: Jira Bot
    text: "PROJ-4521 assigned to Christopher: Update Q3 forecast slides. Due: Friday."
  - channel: CBUDGET
    user: Marcus Thorne
    text: "Re: Q4 contractor budget — what's the headcount breakdown? Can we split across Q3/Q4?"
  - channel: CBUDGET
    user: Marcus Thorne
    text: "Budget approval needed by EOD Thursday or we lose the headcount slot. Finance closes the window Friday morning."
  - channel: CGENERAL
    user: Rachel Chen
    text: "Board deck needs Christopher's sign-off before Wednesday print deadline. Sections 3 and 7 are new since last review."
  - channel: CLEGAL
    user: Sarah Jenkins
    ts: "1724611200"
    text: "Legal hold on Orion dataset — cannot proceed with deletion until Christopher confirms retention scope. Awaiting response since Friday."
  - channel: CGENERAL
    user: Rachel Chen
    text: "Davos panel prep: moderator questions arrived. Need your talking points on global supply chain resilience by Friday."
  - channel: CGENERAL
    user: Elena Rostova
    text: "New global return-to-office policy was internally announced in EMEA. Early sentiment analysis shows mixed reception — 60% supportive, 30% concerned about commute."

smolclaw_seeds:
  - kind: gmail
    from: sarah.jenkins@company.com
    subject: "Approval needed: European cloud acquisition — DOJ terms"
    body: |
      DOJ has issued final concession terms for the Meridian-CloudVault acquisition. They're requesting three items: 1) Nordic divestiture within 18 months, 2) API mandate for open ecosystem access, 3) Cross-sell cap until pipeline matures. I recommend accepting all three. The Nordic divestiture recovers at current multiples, the API mandate aligns with our open ecosystem strategy, and the cross-sell cap expires before the pipeline matures. General Counsel needs your decision by noon to hit the press embargo at 1pm. Marcus (CFO) flagged a $240M write-down on the Nordic divestiture in Slack yesterday.
  - kind: gmail
    from: elena.rostova@company.com
    subject: "URGENT: Acme Corp threatening contract pull — $5M at risk"
    body: |
      Acme Corp's VP of Engineering called this morning threatening to pull their $5M annual contract unless we provide an exec-level response to their service degradation complaints by end of day. They've been a customer since 2019 and this is their renewal quarter. I've prepared talking points but they specifically asked for someone at Christopher's level.
  - kind: gmail
    from: ceo-office@company.com
    subject: "Weekly Digest: Company Newsletter Aug 17"
    body: |
      This week: Q3 all-hands scheduled for Sept 5. New hires: welcome 12 new team members across engineering and sales. Parking policy update: visitor spots now require pre-registration. Summer picnic photos in the gallery.
  - kind: gmail
    from: david.park@company.com
    subject: "ACTION: Vendor contract expires tomorrow — signature needed"
    body: |
      The CloudSync vendor contract expires tomorrow (Tuesday). Without a signed renewal, we auto-renew at 15% higher rate ($180K vs $156K). I've negotiated the renewal at current terms but need your signature on the DocuSign by 5pm today. This is a routine renewal but the cost difference is material.
  - kind: gmail
    from: sarah.jenkins@company.com
    subject: "Q4 contractor hire — budget request $150K"
    body: |
      Requesting approval for $150K to hire 3 contractors for the legal team in Q4. The M&A pipeline has doubled since Q2 and the current team is at 140% capacity. Without additional support we'll miss diligence deadlines on two active deals.
  - kind: gmail
    from: sarah.jenkins@company.com
    subject: "Re: Q4 contractor hire — revised numbers"
    body: |
      Updated based on Marcus's feedback: $90K in Q4 for 2 contractors starting October, $60K deferred to Q1 for the third. Headcount breakdown: 1 corporate counsel, 1 IP specialist (Q4), 1 regulatory analyst (Q1). This splits the budget impact across two quarters as requested.
  - kind: calendar
    summary: "Decision deadline — Meridian acquisition sign-off"
    description: "Christopher must provide sign-off on DOJ concession terms by noon. Press embargo lifts at 1pm. Sarah Jenkins (Legal) and Marcus Thorne (CFO) to be available for questions."
  - kind: calendar
    summary: "Q3 Earnings Call Prep — Goldman/Morgan Stanley"
    description: "Pre-call with IR team. Analysts from Goldman and Morgan Stanley have signaled they will grill on AI infrastructure spend and margin trajectory. Prep materials from Rachel Chen."
  - kind: calendar
    summary: "Final Interview: EVP of Global Logistics"
    description: "Final round interview with the top candidate for EVP of Global Logistics. Forge cross-referenced resume against internal leadership competency model. Decision needed within 48 hours of interview."
EOF

# --- Case: daily-briefing ---
cat > "${EVAL_DIR}/cases/daily-briefing/input.yaml" <<'EOF'
prompt: "/daily-briefing"
EOF

cat > "${EVAL_DIR}/cases/daily-briefing/annotations.yaml" <<'EOF'
expected_top_of_mind:
  - "European cloud acquisition — DOJ deadline at noon"
  - "Acme Corp escalation — $5M contract at risk"
  - "Board deck sign-off — Wednesday deadline"
  - "Vendor contract expires tomorrow (David Park)"
  - "Legal hold on Orion dataset — 3 days old, no reply"
expected_excluded:
  - "Birthday bot message"
  - "Jira bot notification"
  - "Company newsletter"
expected_first: "European cloud acquisition"
EOF

# --- Case: analysis-panel ---
cat > "${EVAL_DIR}/cases/analysis-panel/input.yaml" <<'EOF'
prompt: "/analysis Analyze the European cloud acquisition (Meridian-CloudVault) card from the daily briefing. Read all relevant Slack messages, emails, and calendar events."
EOF

cat > "${EVAL_DIR}/cases/analysis-panel/annotations.yaml" <<'EOF'
expected_facts:
  - "DOJ requested three concessions for the Meridian-CloudVault acquisition"
  - "Decision deadline at noon today"
  - "Sarah Jenkins recommends accepting all three concessions"
  - "$240M write-down risk on Nordic divestiture flagged by Marcus Thorne"
  - "APAC counsel reviewing final IP transfer clause, ETA 11am"
  - "Press embargo at 1pm today"
expected_synthesis:
  - "Flags conflict: Sarah recommends accepting vs Marcus flags write-down risk"
  - "Surfaces hidden constraint: APAC counsel review must complete before commit"
  - "Provides recommendation with appropriate caveats"
EOF

chmod +x "$0"

echo "COS eval bootstrapped:"
echo "  eval.yaml:       ${EVAL_DIR}/eval.yaml"
echo "  chief-of-staff:  ${COS_PKG}/"
echo "  scene:           ${EVAL_DIR}/scenes/monday-acquisition.yaml"
echo "  cases:           daily-briefing (/daily-briefing), analysis-panel (/analysis)"
echo "  judges:          5 LLM rubric (1-5 scale) + 2 deterministic"
echo ""
echo "Run with:"
echo "  ./examples/run-openclaw-forge-cos-agent-eval.sh"
