#!/usr/bin/env bash
# Repo-editing cell for the anova-example (driven by the cli runner).
#
# Checks out models-as-a-service at a base commit, lets the agent implement the
# task's fix (optionally with a cognee MCP), and captures the change as
# output/solution.diff for the solution_quality judge to score against oracle.diff.
#
# Usage: solve.sh <workspace> <output_dir> <model> [mcp_config.json]
#
# Env:
#   MAAS_REPO_URL    (default https://github.com/opendatahub-io/models-as-a-service)
#   MAAS_BASE_COMMIT (default a24c8c8)
#   AGENT_CMD        override the agent invocation (used by tests / other agents)
set -uo pipefail

ws="$1"; out="$2"; model="$3"; mcp="${4:-}"
mkdir -p "$out"
repo="$ws/repo"
url="${MAAS_REPO_URL:-https://github.com/opendatahub-io/models-as-a-service}"
base="${MAAS_BASE_COMMIT:-a24c8c8}"

# Fresh checkout at the base commit (nested repo, so the harness workspace git
# is untouched; the diff is captured explicitly below).
rm -rf "$repo"; mkdir -p "$repo"
git -C "$repo" init -q
git -C "$repo" remote add origin "$url"
if ! git -C "$repo" fetch -q --depth 1 origin "$base" 2>/dev/null; then
  git -C "$repo" fetch -q origin
fi
git -C "$repo" checkout -q "$base" 2>/dev/null || git -C "$repo" checkout -q FETCH_HEAD
git -C "$repo" -c user.email=eval@local -c user.name=eval commit -qm base --allow-empty >/dev/null 2>&1 || true

# The task prompt is the case input (staged into the workspace by eval-run).
prompt="$(python3 -c "import yaml;print(yaml.safe_load(open('$ws/input.yaml')).get('prompt',''))" 2>/dev/null)"
[ -n "$prompt" ] || prompt="Implement the fix described in the task."

# MCP flag only when a real config file is present (context=none -> empty servers).
mcp_flag=""
[ -n "$mcp" ] && [ -f "$mcp" ] && mcp_flag="--mcp-config $mcp"

# Run the agent inside the checked-out repo. AGENT_CMD lets tests stub this out.
if [ -n "${AGENT_CMD:-}" ]; then
  ( cd "$repo" && AGENT_PROMPT="$prompt" AGENT_MCP="$mcp_flag" AGENT_MODEL="$model" \
      bash -c "$AGENT_CMD" ) > "$out/agent.log" 2>&1 || true
else
  ( cd "$repo" && claude --print --permission-mode acceptEdits \
      --model "$model" $mcp_flag "$prompt" ) > "$out/agent.log" 2>&1 || true
fi

# Capture the agent's edits as the solution diff.
git -C "$repo" add -A
git -C "$repo" diff --cached > "$out/solution.diff"
echo "solve.sh: wrote $out/solution.diff ($(wc -l < "$out/solution.diff") lines)"
