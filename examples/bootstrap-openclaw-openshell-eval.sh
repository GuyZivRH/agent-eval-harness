#!/usr/bin/env bash
# Create eval/openclaw-openshell/ (eval.yaml + three demo cases) for the
# OpenShell + Quay OpenClaw e2e guide. Idempotent: overwrites the demo files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="${ROOT}/eval/openclaw-openshell"

mkdir -p \
  "${EVAL_DIR}/cases/case-001" \
  "${EVAL_DIR}/cases/case-002" \
  "${EVAL_DIR}/cases/case-003"

cat > "${EVAL_DIR}/eval.yaml" <<'EOF'
name: openclaw-openshell-test

runner:
  type: openclaw
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

models:
  judge: claude-sonnet-4-5@20250929  # LLM judge runs on host (not in sandbox)

dataset:
  path: cases
  schema: |
    Each case has:
    - input.yaml: prompt (the question to ask the agent)
    - annotations.yaml: expected (the expected answer for correctness check)

outputs:
  - path: output
    schema: |
      response.txt: The agent's final response text

judges:
  - name: correct_answer
    check: |
      ann = outputs.get("annotations", {})
      expected = ann.get("expected", "")
      response = outputs.get("output_content", "") or ""
      return expected.lower() in response.lower()
    feedback_type: bool

  - name: llm_correctness
    llm_rubric: |
      Evaluate whether the agent's response correctly answers the question.

      Question: {{ inputs }}

      Agent Response: {{ outputs.output_content }}

      Expected Answer: {{ outputs.annotations.expected }}

      Is the response correct and appropriate?
    feedback_type: bool

  - name: response_received
    check: |
      response = outputs.get('output_content', '') or ''
      return len(response) > 0
    feedback_type: bool

  - name: no_error
    check: |
      stderr = outputs.get('stderr', '') or ''
      return 'error' not in stderr.lower()
    feedback_type: bool

thresholds:
  correct_answer:
    min_pass_rate: 0.66
  llm_correctness:
    min_pass_rate: 0.66
  response_received:
    min_pass_rate: 1.0
EOF

cat > "${EVAL_DIR}/cases/case-001/input.yaml" <<'EOF'
prompt: "What is the capital of France? Answer in one word."
EOF
cat > "${EVAL_DIR}/cases/case-001/annotations.yaml" <<'EOF'
expected: Paris
EOF

cat > "${EVAL_DIR}/cases/case-002/input.yaml" <<'EOF'
prompt: "What is 15 + 27? Just give the number."
EOF
cat > "${EVAL_DIR}/cases/case-002/annotations.yaml" <<'EOF'
expected: "42"
EOF

cat > "${EVAL_DIR}/cases/case-003/input.yaml" <<'EOF'
prompt: "What color is the sky on a clear day? One word answer."
EOF
cat > "${EVAL_DIR}/cases/case-003/annotations.yaml" <<'EOF'
expected: Blue
EOF

echo "Wrote ${EVAL_DIR}/eval.yaml and cases/case-001..003"
ls -la "${EVAL_DIR}/eval.yaml" "${EVAL_DIR}/cases"/case-00*/{input,annotations}.yaml
