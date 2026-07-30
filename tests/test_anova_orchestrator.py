"""Tests for the eval-anova matrix orchestrator (fan-out over eval-run).

The per-cell execution is stubbed, so these validate the orchestration logic —
the grid loop, factor→param mapping, condition.json stamping, dry-run cost,
--analyze-only, and per-cell failure tolerance — without any real agent runs.
"""

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import yaml

_scripts_dir = str(Path(__file__).parent.parent / "skills" / "eval-anova" / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

import orchestrate as O  # noqa: E402
from agent_eval.matrix import MatrixBuilder  # noqa: E402


def _stub(record):
    """A run_cell_fn that records its call and writes a standard summary.yaml."""
    def run_cell_fn(*, config_path, run_id, output_dir, model, effort,
                    subagent_model, cases, extra_env):
        record.append({"run_id": run_id, "model": model, "effort": effort,
                       "subagent": subagent_model, "cases": list(cases),
                       "env": extra_env})
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        per_case = {c: {"quality": {"value": 4, "judge_type": "numeric"}} for c in cases}
        (out / "summary.yaml").write_text(yaml.dump({"run_id": run_id, "per_case": per_case}))
        (out / "run_result.json").write_text(json.dumps({"model": model, "cost_usd": 0.1}))
    return run_cell_fn


def test_fan_out_one_run_per_cell_maps_factors_and_stamps(tmp_path):
    conds = MatrixBuilder.expand_full_factorial({"model": ["a", "b"], "effort": ["low", "high"]})
    rec = []
    runs_dir = tmp_path / "runs" / "myeval"
    produced = O.fan_out(NS(models=NS(skill=None)), "eval.yaml", conds, ["c1"],
                         replications=1, runs_dir=runs_dir, run_cell_fn=_stub(rec))

    assert len(produced) == 4 == len(rec)
    assert {(r["model"], r["effort"]) for r in rec} == {
        ("a", "low"), ("a", "high"), ("b", "low"), ("b", "high")}
    # each run carries a condition.json with its levels
    cj = json.loads((produced[0] / "condition.json").read_text())
    assert set(cj["levels"]) == {"model", "effort"}
    # AGENT_EVAL_RUNS_DIR passed to eval-run is the runs base (parent of eval dir)
    assert rec[0]["env"]["AGENT_EVAL_RUNS_DIR"] == str(runs_dir.parent)


def test_replications_produce_distinct_run_ids(tmp_path):
    conds = MatrixBuilder.expand_full_factorial({"model": ["a"]})
    rec = []
    O.fan_out(NS(models=NS(skill=None)), "eval.yaml", conds, ["c1"],
              replications=3, runs_dir=tmp_path / "r" / "e", run_cell_fn=_stub(rec))
    ids = {r["run_id"] for r in rec}
    assert len(ids) == 3  # r1/r2/r3 suffixes keep them distinct


def test_unmapped_factor_warns(tmp_path, caplog):
    conds = MatrixBuilder.expand_full_factorial({"model": ["a"], "temperature": ["0.0", "1.0"]})
    with caplog.at_level(logging.WARNING):
        O.fan_out(NS(models=NS(skill=None)), "eval.yaml", conds, ["c1"],
                  replications=1, runs_dir=tmp_path / "r" / "e", run_cell_fn=_stub([]))
    assert any("temperature" in r.message.lower() for r in caplog.records)


def test_cell_failure_is_skipped(tmp_path):
    conds = MatrixBuilder.expand_full_factorial({"model": ["a", "b"]})

    def flaky(*, model, output_dir, cases, run_id, **_):
        if model == "a":
            raise RuntimeError("boom")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.yaml").write_text(yaml.dump({"run_id": run_id, "per_case": {}}))

    produced = O.fan_out(NS(models=NS(skill=None)), "e.yaml", conds, ["c1"],
                         replications=1, runs_dir=tmp_path / "r" / "e", run_cell_fn=flaky)
    assert len(produced) == 1  # only the "b" cell survived


_EVAL_YAML = """
execution:
  mode: case
  skill: myskill
  arguments: "{prompt}"
  max_budget_usd: 2.0
runner:
  type: claude-code
dataset:
  path: dataset
  schema: "cases"
judges:
  - name: quality
    check: |
      return True, "ok"
matrix:
  factors:
    model:
      - claude-opus-4-8
      - claude-sonnet-4-6
  replications: 1
"""


def _project(tmp_path):
    for c in ("c1", "c2"):
        (tmp_path / "dataset" / c).mkdir(parents=True)
        (tmp_path / "dataset" / c / "input.yaml").write_text("prompt: hi\n")
    (tmp_path / "eval.yaml").write_text(_EVAL_YAML)
    return str(tmp_path / "eval.yaml")


def test_main_dry_run_estimates_cost_without_executing(tmp_path, monkeypatch, capsys):
    cfg = _project(tmp_path)
    monkeypatch.setenv("AGENT_EVAL_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(O, "_run_eval_for_condition", _stub([]))

    assert O.main(["--config", cfg, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Total runs: 4" in out
    assert "$0.00" not in out  # cost is estimated, not silently zero
    assert not (tmp_path / "runs").exists()  # nothing executed


def test_main_run_then_analyze_writes_artifact(tmp_path, monkeypatch):
    cfg = _project(tmp_path)
    monkeypatch.setenv("AGENT_EVAL_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(O, "_run_eval_for_condition", _stub([]))

    assert O.main(["--config", cfg, "--no-report"]) == 0
    artifact = tmp_path / "runs" / "myskill" / "anova.json"
    assert artifact.exists()

    # --analyze-only re-analyses existing runs (no execution needed)
    artifact.unlink()
    assert O.main(["--config", cfg, "--analyze-only", "--no-report"]) == 0
    assert artifact.exists()
