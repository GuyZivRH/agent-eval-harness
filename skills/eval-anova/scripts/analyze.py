"""Post-experiment analysis — ANOVA + Pareto + archival."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from agent_eval.archive import ResultsArchiver
from agent_eval.composite import aggregate_replications
from agent_eval.stats import ANOVA_AVAILABLE

logger = logging.getLogger(__name__)


def build_results_dataframe(
    run_results: list[Any],
) -> pd.DataFrame:
    """Convert RunResult list to a DataFrame for statistical analysis."""
    rows = []
    for r in run_results:
        row = {
            "case_id": r.case_id,
            "replication": r.replication,
            "composite": r.composite,
            "condition_id": r.condition.condition_id,
        }
        row.update(r.condition.levels)
        rows.append(row)
    return pd.DataFrame(rows)


def analyze_experiment(
    run_results: list[Any],
    factors: list[str],
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Run statistical analysis on experiment results.

    Uses repeated-measures ANOVA for single-factor designs,
    mixed-effects model for multi-factor designs.
    """
    if not ANOVA_AVAILABLE:
        raise ImportError(
            "ANOVA dependencies not installed. "
            "Install with: pip install agent-eval-harness[anova]"
        )

    from agent_eval.stats.anova import mixed_effects_anova, repeated_measures_anova
    from agent_eval.stats.pareto import pareto_frontier

    df = build_results_dataframe(run_results)

    # Repeated-measures / mixed-effects ANOVA assume a fully-crossed design;
    # pingouin/statsmodels silently drop (listwise) any case missing from a
    # condition, which would leave the reported case count overstating what was
    # actually analysed. Restrict to cases present under every condition and
    # record the rest explicitly so the design/report stay honest.
    df, common_cases, excluded_cases = _restrict_to_common_cases(df)
    if excluded_cases:
        logger.warning(
            "Excluding %d case(s) not present under every condition: %s",
            len(excluded_cases), ", ".join(excluded_cases),
        )

    if len(factors) == 1:
        anova_result = repeated_measures_anova(df, factor=factors[0], alpha=alpha)
    else:
        anova_result = mixed_effects_anova(df, factors=factors, alpha=alpha)

    condition_summaries = []
    for cid, group in df.groupby("condition_id"):
        scores = group["composite"].tolist()
        agg = aggregate_replications(scores)
        levels = {f: group[f].iloc[0] for f in factors if f in group.columns}
        condition_summaries.append({
            "condition_id": cid,
            "levels": levels,
            # Factor levels are also flattened to top level (e.g. "model") so
            # the report renderer can read them directly without unpacking
            # "levels". Keep "levels" too for programmatic consumers.
            **levels,
            **agg,
        })

    # TODO: pareto_frontier requires a distinct cost metric (tokens, API cost,
    # duration) per condition. Until cost data is tracked, skip the call —
    # using cost_key == quality_key == "mean" is a no-op (no domination possible).
    frontier = condition_summaries

    # Report-ready blocks so report.py can render directly from analysis.json
    # without an external driver reshaping the output.
    design = _build_design(df, factors)
    if excluded_cases:
        design["excluded_cases"] = excluded_cases
    per_case = _build_per_case(df, factors)

    return {
        "anova": anova_result,
        "condition_summaries": condition_summaries,
        "pareto_frontier": frontier,
        "design": design,
        "per_case": per_case,
        "excluded_cases": excluded_cases,
        "n_runs": len(run_results),
        "n_conditions": len(condition_summaries),
    }


def _restrict_to_common_cases(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Keep only cases present under *every* condition (a balanced design).

    Returns ``(filtered_df, common_cases, excluded_cases)``. If the frame lacks
    the needed columns, is empty, or has a single condition, nothing is
    excluded. If no case is shared across all conditions the frame is returned
    unchanged (the ANOVA guards then flag the degenerate design).
    """
    if not {"condition_id", "case_id"}.issubset(df.columns) or df.empty:
        return df, [], []
    case_sets = [set(g["case_id"]) for _, g in df.groupby("condition_id")]
    all_cases = set().union(*case_sets)
    common = set(all_cases)
    for s in case_sets:
        common &= s
    excluded = sorted(str(c) for c in (all_cases - common))
    if not excluded:
        return df, sorted(str(c) for c in all_cases), []
    if not common:
        return df, [], excluded
    filtered = df[df["case_id"].isin(common)].copy()
    return filtered, sorted(str(c) for c in common), excluded


def _build_design(df: pd.DataFrame, factors: list[str]) -> dict[str, Any]:
    """Derive the experiment design (factors/levels, case count, reps)."""
    factor_levels = {
        f: sorted(df[f].dropna().unique().tolist())
        for f in factors
        if f in df.columns
    }
    n_cases = int(df["case_id"].nunique()) if "case_id" in df.columns else 0
    # Replications = the largest number of rows for any condition×case pair.
    if {"condition_id", "case_id"}.issubset(df.columns):
        replications = int(df.groupby(["condition_id", "case_id"]).size().max())
    else:
        replications = 1
    return {
        "factors": factor_levels,
        "n_cases": n_cases,
        "replications": replications,
    }


def _build_per_case(df: pd.DataFrame, factors: list[str]) -> dict[str, Any]:
    key_cols = [factor for factor in factors if factor in df.columns]
    if not key_cols and "condition_id" in df.columns:
        key_cols = ["condition_id"]
    if not key_cols or "case_id" not in df.columns:
        return {}

    per_case: dict[str, dict[str, float]] = {}
    for keys, group in df.groupby([*key_cols, "case_id"], dropna=False):
        values = keys if isinstance(keys, tuple) else (keys,)
        factor_values = values[:-1]
        case_id = values[-1]
        if len(key_cols) == 1:
            condition_key = str(factor_values[0])
        else:
            condition_key = _condition_key(key_cols, factor_values)
        per_case.setdefault(condition_key, {})[str(case_id)] = float(
            group["composite"].mean()
        )
    return per_case


def _condition_key(factors: list[str], values: tuple[Any, ...]) -> str:
    return ", ".join(f"{factor}={value}" for factor, value in zip(factors, values))


def archive_results(
    experiment_id: str,
    analysis: dict[str, Any],
    run_results: list[Any],
    repo_path: Path,
) -> Path:
    """Archive experiment results to the results repo."""
    archiver = ResultsArchiver(repo_path=repo_path)

    data = {
        "experiment_id": experiment_id,
        "analysis": _make_serializable(analysis),
        "n_runs": len(run_results),
    }

    return archiver.archive_experiment(experiment_id, data, fallback=True)


def _make_serializable(obj: Any) -> Any:
    """Convert non-serializable types for JSON output."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(i) for i in obj]
    if isinstance(obj, float) and (obj != obj):  # NaN check
        return None
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return obj
