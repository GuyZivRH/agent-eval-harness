---
name: eval-anova
description: Run Design-of-Experiments (DoE) evaluations with ANOVA statistical analysis. Compares agent configurations across factorial experiment designs with repeated-measures statistics that account for case difficulty.
---

# eval-anova

Run a full-factorial experiment comparing agent configurations (models, effort levels, prompts) across shared test cases, then analyze results with repeated-measures ANOVA.

## Usage

```
python3 ${CLAUDE_SKILL_DIR}/scripts/orchestrate.py --config eval.yaml               # run → analyze → report
python3 ${CLAUDE_SKILL_DIR}/scripts/orchestrate.py --config eval.yaml --dry-run     # design + cost estimate, no execution
python3 ${CLAUDE_SKILL_DIR}/scripts/orchestrate.py --config eval.yaml --analyze-only  # re-analyze existing runs + re-render
```

New to this skill? See [QUICKSTART.md](QUICKSTART.md) for from-scratch setup and run steps, and [`eval/anova-example/`](../../eval/anova-example/) for a self-contained worked example (with committed sample runs you can analyze offline).

## How it works

eval-anova is **not** its own executor — it wraps `/eval-run` in a matrix loop:

- **eval-run** stays the single-condition primitive (one model/config → one run with a
  `summary.yaml`). eval-anova runs it **once per matrix cell** (condition × replication), so every
  cell is a standard run under `$AGENT_EVAL_RUNS_DIR/<eval-name>/`, tagged with a `condition.json`
  recording its factor levels.
- **Statistics** are computed over those runs' `summary.yaml` files (`analyze.py` →
  `anova.json`): each case's composite uses the harness's canonical reward composition (the
  eval.yaml `reward:` section, else boolean-gate + normalised-numeric average), then
  repeated-measures / mixed-effects ANOVA + a cost/quality Pareto frontier.
- **The report** is `/eval-compare`, which eval-anova invokes over the runs. eval-compare surfaces
  the ANOVA/Pareto stats automatically when it finds `anova.json`, and stays a standalone
  descriptive comparison when it does not.

Because the stats read standard `summary.yaml` runs, you can also analyze runs produced elsewhere
(e.g. a CI fan-out that runs `/eval-run` per model) — just point `--analyze-only` at their
directory.

## Prerequisites

Install ANOVA dependencies:

```bash
pip install -e ".[anova]"
```

Results archival is optional — set `RHAI_RESULTS_REPO=/path/to/results` to archive experiments to a
git repo (a per-user temp dir is used as a fallback when unset).

## Workflow

1. **Design**: Define factors and levels in your eval YAML's `matrix:` section (`--dry-run` prints
   the grid + a cost estimate).
2. **Execute**: For each condition × replication, drive `/eval-run` (workspace → execute → collect
   → score) → one standard run + `summary.yaml`, tagged with `condition.json`.
3. **Analyze**: Repeated-measures / mixed-effects ANOVA + cost/quality Pareto over the runs →
   `anova.json` (`--analyze-only` runs just this over existing runs).
4. **Report**: `/eval-compare` renders the cross-condition comparison, including the statistics
   section, from the runs + `anova.json`.

## Reports

The orchestrator invokes `/eval-compare` automatically. To (re-)render from existing artifacts:

```bash
# Comparison report (leaderboard, heatmap, + the ANOVA/Pareto stats section if anova.json exists):
python3 ${CLAUDE_PLUGIN_ROOT}/skills/eval-compare/scripts/compare.py generate $AGENT_EVAL_RUNS_DIR/<eval-name>

# Statistics-forward deep view for one experiment (condition means, F / p / η², per-case matrix):
python3 ${CLAUDE_SKILL_DIR}/scripts/report.py $AGENT_EVAL_RUNS_DIR/<eval-name>   # reads anova.json
```

Both read only on-disk artifacts (`summary.yaml` / `anova.json`) and never re-run the experiment.

## Matrix Configuration

Add a `matrix:` key to your eval YAML:

```yaml
matrix:
  factors:
    model:
      - claude-sonnet-4-20250514
      - claude-haiku-4-5-20251001
    effort:
      - low
      - high
  replications: 3
```

See `references/matrix-schema.md` for the full schema.

## Statistical Methods

- **Repeated-measures ANOVA** (default): Accounts for case difficulty as a blocking factor. Correct when the same cases are evaluated under all conditions.
- **Mixed-effects model**: For multi-factor designs with crossed random effects.
- **One-way ANOVA**: Only for independent samples (cases NOT reused). Rarely appropriate.

See `prompts/interpret-anova.md` for guidance on interpreting results.
