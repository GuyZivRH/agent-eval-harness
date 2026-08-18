"""OpenShell backend orchestrator.

Usage: python -m agent_eval.openshell.run --config eval.yaml --model <model> --run-id <id>

Output written to: $AGENT_EVAL_RUNS_DIR/<eval-name>/<run-id>/
"""

import agent_eval._bootstrap  # noqa: F401 - required for entry points

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from agent_eval.agent.openclaw import build_openclaw_argv, parse_openclaw_to_case_dict
from agent_eval.config import EvalConfig
from agent_eval.openshell.sandbox import OpenShellSandbox

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parents[2] / "skills" / "eval-run" / "scripts"

# Environment variables to forward to sandbox (mirrors Harbor's _FORWARD_ENV)
_FORWARD_ENV = (
    # Provider config
    "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION",
    "GOOGLE_CLOUD_PROJECT", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION",
    "OPENAI_BASE_URL", "OPENAI_MODEL",
    # API keys
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "OPENAI_API_KEY",
)


def _child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for a spawned python child.

    Each child is a fresh ``python3 script.py`` entry that has to activate
    ``.eval-venv`` itself. The bootstrap sentinel is designed to survive
    ``os.execv`` *within* one process; letting it cross into a child would make
    the child short-circuit activation and run without the venv's site-packages.
    """
    env = dict(os.environ)
    if extra:
        env.update(extra)
    # After the overrides, so `extra` cannot put the sentinel back.
    env.pop(agent_eval._bootstrap._SENTINEL, None)
    return env


def _sandbox_env(config: EvalConfig) -> Dict[str, str]:
    """Build environment dict to pass to sandbox exec.

    Forwards API keys, provider config, and merges execution.env + runner.env.
    """
    env = {}
    # Forward allowlisted env vars (API keys, provider config)
    for key in _FORWARD_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    # Merge execution.env and runner.env (runner wins on collision)
    if config.execution.env:
        for key, value in config.execution.env.items():
            if value is not None:
                # Resolve $VAR references
                if isinstance(value, str) and value.startswith("$"):
                    resolved = os.environ.get(value[1:])
                    if resolved:
                        env[key] = resolved
                else:
                    env[key] = str(value)
    if config.runner.env:
        for key, value in config.runner.env.items():
            if value is not None:
                if isinstance(value, str) and value.startswith("$"):
                    resolved = os.environ.get(value[1:])
                    if resolved:
                        env[key] = resolved
                else:
                    env[key] = str(value)
    return env


def _resolve_prompt(config: EvalConfig, case_data: dict) -> str:
    """Resolve prompt template using Jinja2 or str.format().

    Mirrors execute.py's _resolve_arguments for template parity.
    """
    template = config.execution.prompt or config.execution.arguments or ""
    if not template:
        return case_data.get("prompt", "")

    # Auto-detect Jinja2 syntax
    if "{{" in template or "{%" in template:
        try:
            from jinja2 import StrictUndefined, Template, UndefinedError
        except ImportError:
            raise ImportError(
                "Jinja2 is required for {{ }} template syntax. "
                "Install with: pip install jinja2"
            )
        try:
            jinja_template = Template(template, undefined=StrictUndefined)
            return jinja_template.render(input=case_data).strip()
        except UndefinedError as e:
            raise ValueError(f"Undefined variable in template: {e}")
    else:
        # Simple str.format() with {field} placeholders
        import re
        def replacer(match):
            field = match.group(1)
            optional = field.endswith("?")
            if optional:
                field = field[:-1]
            if field in case_data:
                return str(case_data[field])
            elif optional:
                return ""
            else:
                raise ValueError(f"Missing required field: {field}")
        return re.sub(r"\{(\w+\??)\}", replacer, template).strip()


async def run_openshell(
    config_path: Path,
    model: str,
    run_id: str,
    parallelism: int = 1,
    keep_sandbox: bool = False,
    cases: Optional[List[str]] = None,
    no_llm_judges: bool = False,
) -> int:
    """Execute evaluation in OpenShell sandboxes.

    Pipeline: workspace.py -> sandbox lifecycle -> collect -> score -> report -> regression

    Args:
        config_path: Path to eval.yaml.
        model: Model identifier.
        run_id: Run identifier.
        parallelism: Number of concurrent cases.
        keep_sandbox: Keep sandboxes after trial for debugging.
        cases: Optional list of case IDs to run (default: all).
        no_llm_judges: Skip LLM judges.

    Returns:
        Exit code (non-zero on regression).
    """
    config = EvalConfig.from_yaml(config_path)
    sandbox_mgr = OpenShellSandbox.from_env()

    # Project root derived from config location for invocation-directory independence.
    # Subprocesses must run from this directory for correct path resolution.
    project_root = config_path.resolve().parent

    image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE")
    if not image:
        raise RuntimeError(
            "AGENT_EVAL_OPENSHELL_IMAGE environment variable is required"
        )

    runs_dir = (
        Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs")) / config.eval_name()
    )
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_dir = runs_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stage workspaces (subprocess workspace.py, parse WORKSPACE line from stdout)
    workspace_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "workspace.py"),
        "--config",
        str(config_path),
        "--run-id",
        run_id,
    ]
    if cases:
        workspace_cmd.extend(["--cases"] + cases)

    logger.info(f"Staging workspaces: {' '.join(workspace_cmd)}")
    result = subprocess.run(
        workspace_cmd,
        capture_output=True,
        text=True,
        check=True,
        env=_child_env(),
        cwd=project_root,
    )

    workspace_root = None
    for line in result.stdout.splitlines():
        if line.startswith("WORKSPACE:"):
            workspace_root = Path(line.split(":", 1)[1].strip())
            break
    if not workspace_root or not workspace_root.exists():
        raise RuntimeError(
            f"workspace.py did not emit valid WORKSPACE path: {result.stdout}"
        )

    case_dirs = sorted((workspace_root / "cases").iterdir())
    start_time = time.monotonic()

    # 2. Run cases in sandboxes (parallel, with error isolation)
    sem = asyncio.Semaphore(parallelism)
    tasks = [
        _run_case(
            sandbox_mgr, config, case_dir, model, image, output_dir, sem, keep_sandbox
        )
        for case_dir in case_dirs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    per_case = {}
    n_failed = 0
    for case_dir, case_result in zip(case_dirs, results):
        case_id = case_dir.name
        if isinstance(case_result, Exception):
            logger.error(f"Case {case_id} failed: {case_result}")
            per_case[case_id] = {"exit_code": 1, "error": str(case_result)}
            n_failed += 1
        else:
            per_case[case_id] = case_result
            # Count non-zero exits (including timeout 124) as failures
            if case_result.get("exit_code", 0) != 0:
                n_failed += 1

    wall_clock_s = time.monotonic() - start_time

    # 3. Write suite-level run_result.json
    # Aggregate cost and tokens from per_case results
    total_cost = sum(
        c.get("cost_usd", 0) or 0 for c in per_case.values() if isinstance(c, dict)
    )
    total_input = sum(
        c.get("token_usage", {}).get("input", 0) or 0
        for c in per_case.values() if isinstance(c, dict)
    )
    total_output = sum(
        c.get("token_usage", {}).get("output", 0) or 0
        for c in per_case.values() if isinstance(c, dict)
    )

    suite_result = {
        "execution_mode": "openshell",
        "agent": "openshell:openclaw",
        "model": model,
        "exit_code": 0 if n_failed == 0 else 1,
        "n_cases": len(case_dirs),
        "n_failed": n_failed,
        "per_case": per_case,
        "wall_clock_s": round(wall_clock_s, 1),
        "cost_usd": round(total_cost, 4) if total_cost else None,
        "token_usage": {"input": total_input, "output": total_output},
    }
    with open(output_dir / "run_result.json", "w") as f:
        json.dump(suite_result, f, indent=2)

    # 4. Collect outputs (subprocess)
    logger.info("Collecting outputs...")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "collect.py"),
            "--config",
            str(config_path),
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_dir),
        ],
        check=True,
        env=_child_env(),
        cwd=project_root,
    )

    # 5. Score (subprocess judges subcommand)
    logger.info("Running judges...")
    score_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "score.py"),
        "judges",
        "--run-id",
        run_id,
        "--config",
        str(config_path),
        "--workspace",
        str(workspace_root),
        "--model",
        model,
    ]
    if no_llm_judges:
        score_cmd.append("--no-llm-judges")
    subprocess.run(score_cmd, check=True, env=_child_env(), cwd=project_root)

    # 6. Generate report (subprocess)
    logger.info("Generating report...")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "report.py"),
            "--run-id",
            run_id,
            "--config",
            str(config_path),
        ],
        check=True,
        env=_child_env(),
        cwd=project_root,
    )

    # 7. Regression detection (in-process, like Harbor/EvalHub)
    if config.thresholds:
        summary_path = output_dir / "summary.yaml"
        if summary_path.exists():
            summary = yaml.safe_load(summary_path.read_text())
            spec = importlib.util.spec_from_file_location(
                "score", SCRIPTS_DIR / "score.py"
            )
            score_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(score_mod)

            regressions = score_mod.detect_regressions(
                summary.get("judges", {}), config.thresholds
            )
            if regressions:
                for r in regressions:
                    logger.warning(
                        f"REGRESSION [{r.judge_name}] {r.metric}: "
                        f"{r.baseline_value} -> {r.current_value}"
                    )
                return 1

    logger.info(f"Run complete: {output_dir}")

    # Exit non-zero if any cases failed (exception or non-zero exit code)
    if n_failed > 0:
        logger.warning(f"{n_failed}/{len(case_dirs)} cases failed")
        return 1

    return 0


async def _run_case(
    sandbox: OpenShellSandbox,
    config: EvalConfig,
    staged_case: Path,
    model: str,
    image: str,
    output_dir: Path,
    sem: asyncio.Semaphore,
    keep: bool,
) -> dict:
    """Run single case in sandbox.

    - Outputs (config.outputs[].path) -> staged_case (workspace) for collect.py
    - Logs (stdout.log, stderr.log, run_result.json) -> output_dir/cases/<id>/ directly

    Args:
        sandbox: OpenShellSandbox instance.
        config: EvalConfig instance.
        staged_case: Path to staged case directory (workspace_root/cases/<id>).
        model: Model identifier.
        image: Container image with OpenClaw.
        output_dir: Run output directory (runs/<run-id>/).
        sem: Semaphore for parallelism control.
        keep: Keep sandbox after trial.

    Returns:
        Case result dict for suite per_case.
    """
    case_id = staged_case.name
    case_output = output_dir / "cases" / case_id
    case_output.mkdir(parents=True, exist_ok=True)

    async with sem:
        name = f"aeh-{case_id}-{uuid.uuid4().hex[:8]}"
        start_time = time.monotonic()
        try:
            logger.info(f"Creating sandbox {name} for case {case_id}")
            await sandbox.create(name, image)

            await sandbox.upload(name, staged_case, "/sandbox")

            await sandbox.exec(name, ["mkdir", "-p", "/sandbox/.openclaw-state"])

            effort = config.runner.effort
            if not effort and config.runner.settings:
                effort = config.runner.settings.get("effort")

            cmd = build_openclaw_argv(
                model=model,
                cwd=None,
                timeout_s=config.execution.timeout,
                effort=effort,
                state_dir=Path("/sandbox/.openclaw-state"),
                auth_env_only=True,
            )

            input_yaml_path = staged_case / "input.yaml"
            if input_yaml_path.exists():
                input_yaml = yaml.safe_load(input_yaml_path.read_text()) or {}
            else:
                input_yaml = {}

            # Resolve prompt template (Jinja2 or str.format)
            prompt = _resolve_prompt(config, input_yaml)

            # Build env to forward to sandbox (API keys + config env)
            sandbox_env = _sandbox_env(config)

            logger.info(f"Executing case {case_id} in sandbox {name}")
            timeout = (config.execution.timeout or 600) + 60
            result = await sandbox.exec(
                name,
                cmd,
                workdir="/sandbox",
                stdin=prompt.encode(),
                env=sandbox_env,
                timeout_s=timeout,
            )
            duration_s = time.monotonic() - start_time

            for output in config.outputs or []:
                if output.path:
                    try:
                        await sandbox.download(
                            name, f"/sandbox/{output.path}", staged_case / output.path
                        )
                    except Exception as e:
                        logger.warning(f"Failed to download {output.path}: {e}")

            try:
                await sandbox.download(
                    name, "/sandbox/.openclaw-state", case_output / "trajectory"
                )
            except Exception as e:
                logger.debug(f"No trajectory captured for {case_id}: {e}")

            case_result = parse_openclaw_to_case_dict(
                result.stdout.encode(),
                result.stderr.encode(),
                result.return_code,
                duration_s,
            )

            with open(case_output / "run_result.json", "w") as f:
                json.dump(case_result, f, indent=2)
            (case_output / "stdout.log").write_text(result.stdout)
            (case_output / "stderr.log").write_text(result.stderr)

            logger.info(f"Case {case_id} completed with exit code {result.return_code}")
            return case_result

        finally:
            if not keep:
                await sandbox.delete(name)
            else:
                logger.info(f"Kept sandbox {name}: openshell sandbox connect {name}")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="OpenShell backend for agent-eval-harness"
    )
    parser.add_argument("--config", required=True, help="Path to eval.yaml")
    parser.add_argument("--model", required=True, help="Model to use")
    parser.add_argument("--run-id", help="Run ID (default: timestamp)")
    parser.add_argument("-n", "--parallelism", type=int, default=1)
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        help="Keep sandboxes after trial (or set AGENT_EVAL_OPENSHELL_KEEP_RUN=1)",
    )
    parser.add_argument("--cases", nargs="+", help="Case IDs to run (default: all)")
    parser.add_argument(
        "--no-llm-judges", action="store_true", help="Skip LLM judges"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    keep = args.keep_sandbox or os.environ.get("AGENT_EVAL_OPENSHELL_KEEP_RUN") == "1"
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")

    exit_code = asyncio.run(
        run_openshell(
            config_path=Path(args.config),
            model=args.model,
            run_id=run_id,
            parallelism=args.parallelism,
            keep_sandbox=keep,
            cases=args.cases,
            no_llm_judges=args.no_llm_judges,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
