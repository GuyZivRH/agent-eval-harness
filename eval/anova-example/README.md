# anova-example — a worked `/eval-anova` example

A real Design-of-Experiments example over **4 bugfix PRs from
[opendatahub-io/models-as-a-service](https://github.com/opendatahub-io/models-as-a-service)**.
It runs a **model × context** matrix — comparing models with and without a
[cognee](https://github.com/topoteretes/cognee) knowledge-graph MCP — then runs
mixed-effects ANOVA over the results.

It stays entirely on the **generic path**: a `cli` runner does a local `git`
checkout (no Harbor, no OpenShift, no committed credentials), and the
`context` factor reaches the command through the orchestrator's
`--input-override` (finding #17).

## Files

- `eval.yaml` — the cli-runner eval + a `matrix:` (model × context).
- `solve.sh` — per-cell script: checks out models-as-a-service at the base
  commit, runs the agent (with the cognee MCP when `context=cognee`), and writes
  `output/solution.diff`.
- `mcp-cognee.json` / `mcp-none.json` — the two `context` levels. Point
  `mcp-cognee.json` at your own cognee MCP endpoint (`COGNEE_MCP_URL`).
- `dataset/task-*/` — the four tasks: `input.yaml` (PR prompt), `instruction.txt`,
  and `oracle.diff` (the merged PR the judge scores against).
- `sample-runs/` — committed results so the analysis + report reproduce offline.

## Run it

```bash
pip install -e ".[anova,anthropic]"
export ANTHROPIC_API_KEY=sk-...            # or the Vertex env (see QUICKSTART)
export COGNEE_MCP_URL=http://<your-cognee-mcp>/mcp   # only needed for context=cognee

python3 skills/eval-anova/scripts/orchestrate.py --config eval/anova-example/eval.yaml --dry-run
python3 skills/eval-anova/scripts/orchestrate.py --config eval/anova-example/eval.yaml
```

Each cell (model × context) runs as a **standard eval-run** under
`$AGENT_EVAL_RUNS_DIR/anova-example/<run-id>/` with a `condition.json`; the
orchestrator then writes `anova.json` and renders the `/eval-compare` report
(including the ANOVA/Pareto statistics section).

## Reproduce the analysis offline (no API key, no checkout)

```bash
AGENT_EVAL_RUNS_DIR=eval/anova-example/sample-runs \
  python3 skills/eval-anova/scripts/orchestrate.py \
  --config eval/anova-example/eval.yaml --analyze-only
open eval/anova-example/sample-runs/anova-example/comparison-report/index.html
```

On the sample data the mixed-effects ANOVA finds **model** significant and
**context** not — a clean demonstration of separating two factors' effects.

## How the matrix reaches the run

- `model` maps to the runner's `--model`, so each cell runs a different model.
- `context` is a **non-model factor**. The orchestrator passes it via
  `execute.py --input-override context=<level>`, which merges it into the case's
  `input.yaml`, so `{context}` resolves in the cli command and selects
  `{config_dir}/mcp-{context}.json`. `{config_dir}` is the eval's directory, so
  the command can find `solve.sh` and the MCP configs regardless of cwd.

## Notes

- The live run needs network + the models-as-a-service repo + an API key, so it
  is not exercised in CI; the tests cover config validity, `--dry-run`,
  `solve.sh`'s checkout/diff capture (with a stub agent), and the offline
  `--analyze-only` + report path over `sample-runs/`.
- `MAAS_REPO_URL` / `MAAS_BASE_COMMIT` override the repo and base commit.
