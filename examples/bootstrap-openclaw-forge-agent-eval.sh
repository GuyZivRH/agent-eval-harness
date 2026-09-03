#!/usr/bin/env bash
# Create eval/openclaw-forge-agent/ (eval.yaml + scene + cases) for Forge
# evaluation rubrics — Chief of Staff agent morning briefing.
# Scene-based: seeds once at run start, 2 cases scored by 8 LLM rubric judges.
# Idempotent: overwrites demo files (does not touch eval/runs/).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-forge-agent"

mkdir -p \
  "${EVAL_DIR}/scenes" \
  "${EVAL_DIR}/cases/daily-briefing" \
  "${EVAL_DIR}/cases/analysis-panel"

# --- eval.yaml ---
cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
# Forge evaluation rubrics — Chief of Staff agent producing executive morning briefing
# Scene-based: all messages seeded once at run start from scenes/<name>.yaml
name: forge-eval-rubrics

scene: monday-acquisition

models:
  judge: claude-sonnet-4

runner:
  type: openclaw
  system_prompt: |
    You are an evaluation agent. For this task you MUST use the exec tool to run
    real curl commands against the host mock APIs configured in the environment
    (Slack via SLACK_API_URL, Gmail via GMAIL_API_URL, Calendar via
    CALENDAR_API_URL as present). Never simulate tool results. Never invent API
    responses. Prefer exec over describing shell commands in prose.
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
    - input.yaml: agent prompt for producing a morning briefing or analysis panel
    - annotations.yaml: ground truth expectations (expected items, groupings,
      facts, sources) used by LLM rubric judges

outputs:
  - path: output
    schema: |
      response.txt: agent final response (morning briefing or analysis panel)

judges:
  # --- Prioritization judges (daily-briefing case) ---
  - name: prioritization_recall
    if: "annotations.get('expected_top_of_mind')"
    prompt: |
      You are evaluating a morning briefing produced by a Chief of Staff agent.

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
         but one landed in the wrong section (e.g., FYI instead of Top of Mind).
      3: One expected item is missing from Top of Mind or buried below the fold.
         The other expected items are there.
      2: Two expected items are missing, or the single most critical item is
         missing while smaller items made it.
      1: Something the executive cannot afford to miss — a legal deadline, a
         customer fire, a decision only they can make — is not on the briefing.
    score_range: [1, 5]
    feedback_type: int

  - name: prioritization_precision
    if: "annotations.get('expected_excluded')"
    prompt: |
      You are evaluating a morning briefing produced by a Chief of Staff agent.

      The briefing should NOT include noise in the "Top of Mind" section.
      Noise includes automated notifications, bot messages, newsletters,
      birthday greetings, and social posts.

      Items that MUST be excluded from Top of Mind:
      {% for item in annotations.expected_excluded %}
      - {{ item }}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score PRECISION on a 1-5 scale:
      5: Every card in Top of Mind is something the executive should handle today.
         Newsletters, bot mail, birthday posts, and "nice to know" updates are not
         in Top of Mind — they sit in FYI or do not appear.
      4: One card in Top of Mind is a close call — an exec might have put it in
         FYI — but it is still a real work item, not a newsletter or bot.
      3: One or two cards in Top of Mind should not be there: a bot notification,
         a newsletter, or a social post sitting next to real decisions.
      2: Several cards in Top of Mind are filler (bots, social, mass mail), or
         the ranking hid a time-critical ask.
      1: Top of Mind is mostly filler, or it is empty even though the inbox
         clearly has work that needs the executive.
    score_range: [1, 5]
    feedback_type: int

  - name: prioritization_relevance
    if: "annotations.get('expected_first')"
    prompt: |
      You are evaluating a morning briefing produced by a Chief of Staff agent.

      The briefing's "Top of Mind" section should rank items by stakes and
      urgency, with the highest-stakes item first.

      The highest-stakes item that should be first:
      {{ annotations.expected_first }}

      Items expected in FYI (not Top of Mind):
      {% for item in annotations.expected_fyi %}
      - {{ item }}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score RELEVANCE on a 1-5 scale:
      5: The top card is the highest-stakes item, then the next, and so on.
         Each card's one-line reason is specific and true (who, how long it has
         waited, why only they can unblock it).
      4: Two neighboring cards are swapped (e.g., 2nd and 3rd), but the
         highest-stakes item is still first. The reason line is true but vague
         ("important email" instead of "unanswered 18 hours, client").
      3: The right items are on screen but in the wrong order — newest or
         loudest beat the actual decision. Or there are too many cards with equal
         weight and no demotion. Most cards have no reason line.
      2: A low-stakes item (party invite, all-hands chatter) sits above
         something that needs a decision. Or the reason line is false.
      1: No ranking (just newest-first), or the list is so aggressively filtered
         that the real day is hidden.
    score_range: [1, 5]
    feedback_type: int

  # --- Data connection judges (daily-briefing case) ---
  - name: connection_precision
    if: "annotations.get('expected_separate')"
    prompt: |
      You are evaluating whether a morning briefing correctly keeps unrelated
      messages on separate cards, even when they share a sender or company.

      These items MUST be on separate cards (not merged):
      {% for pair in annotations.expected_separate %}
      - {{ pair.source_a }} vs {{ pair.source_b }} — {{ pair.reason }}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score DATA CONNECTION PRECISION on a 1-5 scale:
      5: Every card is about the same decision, deal, incident, or commitment.
         The agent did not mix in a second issue just because it shares a person
         or company name.
      4: Everything on each card is about one issue, except one extra "fyi /
         thanks" message that does not change the decision.
      3: One message on a card is about a related but different piece of work
         (same company, different decision), or the same issue was split into two
         separate cards that are each internally consistent.
      2: Two different decisions are glued onto one card because they share a
         sender or company. Opening review would blend them into one story.
      1: Messages on a card are not the same job — the card is trying to be two
         or more pieces of work at once.
    score_range: [1, 5]
    feedback_type: int

  - name: connection_recall
    if: "annotations.get('expected_bundles')"
    prompt: |
      You are evaluating whether a morning briefing correctly groups related
      messages into the same card, even when they come from different channels.

      Expected bundles (messages that should be on the same card):
      {% for bundle_name, messages in annotations.expected_bundles.items() %}
      Bundle "{{ bundle_name }}":
      {% for msg in messages %}
        - {{ msg }}
      {% endfor %}
      {% endfor %}

      Agent's briefing:
      {{ outputs.output_content }}

      Score DATA CONNECTION RECALL on a 1-5 scale:
      5: Every message in each expected bundle is attached to the same card,
         including email and Slack when both exist. No duplicate cards for the
         same issue.
      4: The original ask, the latest status, and the email/Slack counterpart are
         all on the card. Only a lightweight ping ("sounds good") is missing.
      3: The core ask is on the card, but one supporting message is missing — a
         follow-up that contains a number, a constraint, or a "do not commit"
         line. A careful reader would still want that message.
      2: A message that actually changes the story is missing: the original
         request, a deadline change, or the Slack update that goes with the email.
         Or the issue is split so review only sees half of it.
      1: Most of the expected bundle is missing or scattered across the briefing.
         The executive would have to hunt the inbox to rebuild what happened.
    score_range: [1, 5]
    feedback_type: int

  # --- Analysis panel judges (analysis-panel case) ---
  - name: analysis_accuracy
    if: "annotations.get('expected_facts')"
    prompt: |
      You are evaluating the analysis panel produced by a Chief of Staff agent
      for a specific card in the executive's morning briefing.

      Facts that MUST appear accurately in the analysis:
      {% for fact in annotations.expected_facts %}
      - {{ fact }}
      {% endfor %}

      The analysis MUST NOT fabricate:
      {% for rule in annotations.must_not_fabricate %}
      - {{ rule }}
      {% endfor %}

      Agent's analysis:
      {{ outputs.output_content }}

      Score ACCURACY on a 1-5 scale:
      5: Names, dates, numbers, and what's at stake match the source messages.
         The analysis does not add facts that are not in those messages.
      4: The facts that would change the decision are right. One small miss (a
         misspelled name, a secondary date) that would not change the call.
      3: Nothing is made up, but a constraint that should affect caution is
         missing (a "do not commit," a conflicting note). The executive would
         still need to open the thread to be safe.
      2: One fact is wrong (even if it is not the main ask), or a missing line
         would have changed how careful they should be.
      1: Invented deadline, number, or source; or the recommendation is the
         opposite of what the messages support.
    score_range: [1, 5]
    feedback_type: int

  - name: analysis_independent_judgment
    if: "annotations.get('expected_synthesis')"
    prompt: |
      You are evaluating whether the analysis panel adds independent value
      beyond paraphrasing the source messages.

      The analysis should demonstrate these forms of synthesis:
      {% for item in annotations.expected_synthesis %}
      - {{ item }}
      {% endfor %}

      Agent's analysis:
      {{ outputs.output_content }}

      Score INDEPENDENT JUDGMENT on a 1-5 scale:
      5: The analysis does work the sender did not already do: what is at stake,
         what you would still verify, and a clear recommendation. Someone could
         walk into a conversation from this screen alone.
      4: There is a real recommendation, but the "what you should still check"
         list is thin. Still more than a paraphrase of the latest email.
      3: A competent summary with a weak or generic recommendation ("you may want
         to follow up"). Some restating of the sender, a little extra synthesis.
         Feels like a typical inbox copilot.
      2: The analysis mostly repeats what the sender already wrote. No independent
         "here's what's actually at stake." Or it recommends something the sources
         do not support, with too much confidence.
      1: Opening the analysis does not help them decide. The panel is just the
         draft, or it dumps them into chat with no analysis.
    score_range: [1, 5]
    feedback_type: int

  - name: analysis_citations
    if: "annotations.get('expected_sources')"
    prompt: |
      You are evaluating whether the analysis panel properly cites its sources
      and distinguishes sourced facts from AI inference.

      Sources that should be cited in the analysis:
      {% for source in annotations.expected_sources %}
      - {{ source }}
      {% endfor %}

      Agent's analysis:
      {{ outputs.output_content }}

      Score CITATIONS on a 1-5 scale:
      5: Each important claim links to a named email or Slack message. The
         analysis marks what was taken from sources vs. what the AI inferred. It
         is obvious that an AI system, not a colleague, is making the
         recommendation.
      4: Sources are named. One claim links to the whole message instead of the
         passage, or one obvious inference is not labeled, but the rest are clean.
      3: There is a source list, but several claims have no link, or "How this
         was made" is generic disclaimer language instead of specific messages.
         Inferred sentences are mixed in with sourced ones and not marked.
      2: Links exist but one of them references the wrong thread. Or wording like
         "Sarah recommends" makes it sound like a person, not the system.
      1: No sources, made-up sources, or guesses presented as if they came from
         the executive's own mail.
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
  connection_precision:
    min_mean: 5.0
  connection_recall:
    min_mean: 5.0
  analysis_accuracy:
    min_mean: 5.0
  analysis_independent_judgment:
    min_mean: 5.0
  analysis_citations:
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
  # Slack messages (14)
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
  # Gmail messages (6)
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

  # Calendar events (3)
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
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent API responses.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN, GMAIL_API_URL, CALENDAR_API_URL.

  Task (READ ONLY — do not post any messages):
  You are a Chief of Staff agent producing the morning briefing for Christopher,
  an executive.
  Read all available Slack channels/DMs, Gmail messages, and Calendar events.

  1) conversations.list with types=public_channel,private_channel,im
  2) conversations.history on each channel to read all messages
  3) GET ${GMAIL_API_URL}users/me/messages to list emails, then GET each message
  4) GET ${CALENDAR_API_URL}calendars/primary/events to list calendar events

  Produce a structured morning briefing with three sections:

  TOP OF MIND — Direct decisions and critical actions requiring Christopher's
  voice today. Each card must have:
    - A title describing the decision/action needed
    - The person who raised it
    - A one-line reason why it needs Christopher specifically and how urgent it is
    - Which sources (Slack channel/DM, email, calendar) it came from
  Group related messages about the same workstream into one card.

  FYI — Active signals and updates to track, file, or flag. Not urgent.

  LOOKING AHEAD — Preparation and context for the next few days.

  Rules:
  - Top of Mind: only items that require Christopher to act or decide today.
  - FYI: real business signals worth knowing (market moves, completed
    operations, policy changes) but no action needed now.
  - Exclude entirely: automated notifications, bot messages, newsletters,
    birthday greetings, and social posts.
  - Rank Top of Mind by stakes and urgency.
  - Group messages about the same workstream into one card even if they
    come from different channels (email + Slack about the same deal = one card).
  - Do not merge messages from the same sender about different topics.
  - Do not call chat.postMessage — this is a read-only task.
EOF

cat > "${EVAL_DIR}/cases/daily-briefing/annotations.yaml" <<'EOF'
expected_top_of_mind:
  - "European cloud acquisition — DOJ deadline at noon"
  - "Acme Corp escalation — $5M contract at risk"
  - "Board deck sign-off — Wednesday deadline"
  - "Vendor contract expires tomorrow (David Park)"
  - "Legal hold on Orion dataset — 3 days old, no reply"
expected_fyi:
  - "APAC data center migration complete"
  - "AeroGlobal earnings miss"
  - "Return-to-office policy — EMEA"
expected_excluded:
  - "Birthday bot message"
  - "Jira bot notification"
  - "Company newsletter"
expected_first: "European cloud acquisition"
expected_bundles:
  acquisition:
    - "Sarah Jenkins email: DOJ terms analysis"
    - "Sarah Jenkins Slack #acquisition-meridian: DOJ terms arrived"
    - "Marcus Thorne Slack #acquisition-meridian: $240M write-down risk"
    - "Sarah Jenkins Slack #acquisition-meridian: APAC counsel constraint"
  budget:
    - "Sarah Jenkins email: Q4 contractor budget request $150K"
    - "Marcus Thorne Slack #budget-approvals: headcount breakdown question"
    - "Sarah Jenkins email: revised numbers $90K Q4 $60K Q1"
    - "Marcus Thorne Slack #budget-approvals: approval deadline EOD Thursday"
expected_separate:
  - source_a: "Elena Rostova email: Acme Corp escalation"
    source_b: "Elena Rostova Slack #office-life: offsite venue"
    reason: "Same sender, different topics — must not merge"
EOF

# --- Case: analysis-panel ---
cat > "${EVAL_DIR}/cases/analysis-panel/input.yaml" <<'EOF'
prompt: |
  CRITICAL: Call the OpenClaw `exec` tool to run real curl commands.
  Do NOT paste bash in markdown instead of calling exec.
  Do NOT invent API responses.

  Env already set: SLACK_API_URL, SLACK_BOT_TOKEN, GMAIL_API_URL, CALENDAR_API_URL.

  Task (READ ONLY — do not post any messages):
  You are a Chief of Staff agent producing a detailed analysis panel (Review)
  for the European cloud acquisition (Meridian-CloudVault) card in the
  executive's morning briefing.

  Read the relevant Slack messages, emails, and calendar events about this
  acquisition, then produce an analysis panel with:

  ANALYSIS — A synthesis paragraph that:
    - States what is at stake (not just restating what the sender wrote)
    - Identifies conflicting information across sources
    - Surfaces any constraints or caveats from follow-up messages
    - Provides a clear recommendation
    - Identifies what Christopher should still verify before deciding

  TIMELINE — Chronological view of how the situation evolved, with dates.

  SOURCES — List each source message used, with:
    - The person who sent it
    - The channel or medium (Slack channel name, email subject, calendar event)
    - A brief quote or reference to the specific claim it supports

  Rules:
  - Every factual claim must be traceable to a named source.
  - Clearly distinguish what comes from sources vs what is your inference.
  - Make it obvious that this analysis is from an AI system, not a colleague.
  - Do not invent deadlines, dollar amounts, or facts not in the sources.
  - Do not call chat.postMessage — this is a read-only task.
EOF

cat > "${EVAL_DIR}/cases/analysis-panel/annotations.yaml" <<'EOF'
expected_facts:
  - "DOJ requested three concessions for the Meridian-CloudVault acquisition"
  - "Decision deadline at noon today"
  - "Sarah Jenkins recommends accepting all three concessions"
  - "$240M write-down risk on Nordic divestiture flagged by Marcus Thorne"
  - "APAC counsel reviewing final IP transfer clause, ETA 11am"
  - "Press embargo at 1pm today"
must_not_fabricate:
  - "Do not invent deadlines not present in sources"
  - "Do not invent dollar amounts not present in sources"
  - "Do not attribute recommendations to people who did not make them"
expected_sources:
  - "Sarah Jenkins email: Approval needed — DOJ terms"
  - "Sarah Jenkins Slack #acquisition-meridian: DOJ terms arrived Friday"
  - "Marcus Thorne Slack #acquisition-meridian: $240M write-down flag"
  - "Sarah Jenkins Slack #acquisition-meridian: APAC counsel constraint"
  - "Calendar: Decision deadline — Meridian acquisition sign-off at noon"
expected_synthesis:
  - "Stakes analysis: connects DOJ terms to business strategy implications"
  - "Flags conflict: Sarah recommends accepting vs Marcus flags write-down risk"
  - "Surfaces hidden constraint: APAC counsel review must complete before commit"
  - "Provides recommendation with appropriate caveats"
  - "Lists what to verify: APAC counsel ETA, write-down impact on deal economics"
EOF

chmod +x "$0"

echo "Forge eval rubrics bootstrapped:"
echo "  eval.yaml:    ${EVAL_DIR}/eval.yaml"
echo "  scene:        ${EVAL_DIR}/scenes/monday-acquisition.yaml"
echo "  cases:        daily-briefing, analysis-panel"
echo "  judges:       8 LLM rubric (1-5 scale) + 2 deterministic"
echo ""
echo "Run with:"
echo "  ./examples/run-openclaw-forge-agent-eval.sh"
