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
from agent_eval.events import (
    build_explicit_openclaw_session_key,
    events_from_openclaw_exec,
    parse_openclaw_session,
    parse_openclaw_trajectory_events,
    resolve_openclaw_session_file,
    resolve_openclaw_session_key_from_list,
)
from agent_eval.openshell.sandbox import OpenShellSandbox

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parents[2] / "skills" / "eval-run" / "scripts"

# Retained OpenClaw state under the sandbox workdir. ``agent exec`` deletes a
# temp state dir on exit unless ``--state-dir`` is set; without retained state
# Quay 2026.7.x has no harvestable trajectory (SQLite is wiped with the temp dir).
_OPENCLAW_STATE_DIR = Path("/sandbox/.openclaw")
_OPENCLAW_TMP_DIR = Path("/sandbox/tmp")

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


async def _harvest_openclaw_events(
    sandbox: OpenShellSandbox,
    name: str,
    *,
    stdout_text: str,
    prompt: str,
    case_id: str,
    case_output: Path,
    sandbox_env: Dict[str, str],
) -> list:
    """Build AEH events from OpenClaw session JSONL, trajectory export, or envelope.

    Preference order:
    1. Legacy ``meta.agentMeta.sessionFile`` JSONL (pre-SQLite OpenClaw)
    2. ``openclaw sessions export-trajectory`` → ``events.jsonl`` (Quay 2026.7.x)
    3. Synthesize user/assistant from the compact ``agent exec`` envelope
    """
    openclaw_json: dict = {}
    try:
        openclaw_json = json.loads(stdout_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        start = (stdout_text or "").find("{")
        if start >= 0:
            try:
                openclaw_json = json.loads(stdout_text[start:])
            except (json.JSONDecodeError, ValueError):
                openclaw_json = {}

    # 1) Legacy session JSONL path
    session_file = resolve_openclaw_session_file(openclaw_json) if openclaw_json else None
    if session_file:
        try:
            cat_result = await sandbox.exec(name, ["cat", session_file])
            if cat_result.return_code == 0 and cat_result.stdout:
                events = parse_openclaw_session(cat_result.stdout)
                if events:
                    return events
        except Exception as e:
            logger.warning(f"Failed to read OpenClaw sessionFile for {case_id}: {e}")

    # 2) SQLite-era trajectory export (requires retained --state-dir)
    session_id = (openclaw_json or {}).get("sessionId") or ""
    if session_id:
        session_key = build_explicit_openclaw_session_key(session_id)
        export_name = f"aeh-{case_id}"
        try:
            export_result = await sandbox.exec(
                name,
                [
                    "openclaw",
                    "sessions",
                    "export-trajectory",
                    "--session-key",
                    session_key,
                    "--workspace",
                    "/sandbox",
                    "--output",
                    export_name,
                    "--json",
                ],
                workdir="/sandbox",
                env=sandbox_env,
                timeout_s=120,
            )
            if export_result.return_code != 0:
                # Fallback: resolve key via sessions list (match sessionId)
                list_result = await sandbox.exec(
                    name,
                    ["openclaw", "sessions", "--json"],
                    workdir="/sandbox",
                    env=sandbox_env,
                    timeout_s=60,
                )
                alt_key = resolve_openclaw_session_key_from_list(
                    list_result.stdout if list_result.return_code == 0 else "",
                    session_id,
                )
                if alt_key and alt_key != session_key:
                    session_key = alt_key
                    export_result = await sandbox.exec(
                        name,
                        [
                            "openclaw",
                            "sessions",
                            "export-trajectory",
                            "--session-key",
                            session_key,
                            "--workspace",
                            "/sandbox",
                            "--output",
                            export_name,
                            "--json",
                        ],
                        workdir="/sandbox",
                        env=sandbox_env,
                        timeout_s=120,
                    )

            if export_result.return_code == 0:
                summary = {}
                try:
                    summary = json.loads(export_result.stdout)
                except (json.JSONDecodeError, TypeError, ValueError):
                    start = (export_result.stdout or "").find("{")
                    if start >= 0:
                        try:
                            summary = json.loads(export_result.stdout[start:])
                        except (json.JSONDecodeError, ValueError):
                            summary = {}

                output_dir = summary.get("outputDir") or (
                    f"/sandbox/.openclaw/trajectory-exports/{export_name}"
                )
                events_path = f"{output_dir.rstrip('/')}/events.jsonl"
                cat_events = await sandbox.exec(name, ["cat", events_path])
                if cat_events.return_code == 0 and cat_events.stdout:
                    # Keep raw export for debugging / offline reparse
                    (case_output / "openclaw-trajectory-events.jsonl").write_text(
                        cat_events.stdout
                    )
                    events = parse_openclaw_trajectory_events(cat_events.stdout)
                    if events:
                        logger.info(
                            "Harvested %d events from OpenClaw trajectory for %s",
                            len(events),
                            case_id,
                        )
                        return events
                else:
                    logger.warning(
                        "Trajectory export for %s succeeded but events.jsonl "
                        "missing at %s (stderr=%s)",
                        case_id,
                        events_path,
                        (export_result.stderr or "")[:300],
                    )
            else:
                logger.warning(
                    "OpenClaw trajectory export failed for %s "
                    "(session_key=%s, code=%s): %s",
                    case_id,
                    session_key,
                    export_result.return_code,
                    (export_result.stderr or export_result.stdout or "")[:400],
                )
        except Exception as e:
            logger.warning(f"OpenClaw trajectory harvest failed for {case_id}: {e}")

    # 3) Compact envelope fallback (answer text only)
    return events_from_openclaw_exec(stdout_text, prompt=prompt)


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

    # Resolve to absolute path once - subprocesses run with different cwd
    abs_config_path = config_path.resolve()

    # Find project root (repository root) for consistent path resolution.
    # Walk up from config file until we find a marker (e.g., .git, pyproject.toml).
    project_root = abs_config_path.parent
    for parent in [abs_config_path.parent] + list(abs_config_path.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            project_root = parent
            break

    image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE")
    if not image:
        raise RuntimeError(
            "AGENT_EVAL_OPENSHELL_IMAGE environment variable is required"
        )

    runs_dir = (
        Path(os.environ.get("AGENT_EVAL_RUNS_DIR", "eval/runs")) / config.eval_name()
    ).resolve()  # Use absolute path to avoid cwd issues
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_dir = runs_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Stage workspaces (subprocess workspace.py, parse WORKSPACE line from stdout)
    workspace_cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "workspace.py"),
        "--config",
        str(abs_config_path),
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
            str(abs_config_path),
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
        str(abs_config_path),
        "--workspace",
        str(workspace_root),
        "--model",
        model,
    ]
    if no_llm_judges:
        score_cmd.append("--no-llm-judges")
    score_result = subprocess.run(score_cmd, env=_child_env(), cwd=project_root)
    score_exit_code = score_result.returncode

    # 6. Generate report (subprocess) - always generate even if scoring had issues
    logger.info("Generating report...")
    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "report.py"),
            "--run-id",
            run_id,
            "--config",
            str(abs_config_path),
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

    report_path = output_dir / "report.html"
    if report_path.exists():
        logger.info(f"Report: {report_path}")
    logger.info(f"Run complete: {output_dir}")

    # Exit non-zero if any cases failed (exception or non-zero exit code)
    if n_failed > 0:
        logger.warning(f"{n_failed}/{len(case_dirs)} cases failed")
        return 1
    
    # Propagate scoring exit code (e.g., regression detection in score.py)
    if score_exit_code != 0:
        return score_exit_code

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
        # OpenShell sandbox names max 19 chars: prefix(2) + hex(8) + dash + digits
        name = f"e-{uuid.uuid4().hex[:8]}-{case_id[-3:]}"
        start_time = time.monotonic()
        try:
            logger.info(f"Creating sandbox {name} for case {case_id}")
            await sandbox.create(name, image)

            # OpenShell nests directory uploads: local case dir → /sandbox/<case_id>/
            await sandbox.upload(name, staged_case, "/sandbox")

            input_yaml_path = staged_case / "input.yaml"
            if input_yaml_path.exists():
                input_yaml = yaml.safe_load(input_yaml_path.read_text()) or {}
            else:
                input_yaml = {}

            # Resolve prompt template (Jinja2 or str.format)
            prompt = _resolve_prompt(config, input_yaml)
            system_prompt = getattr(config.runner, "system_prompt", None)
            if system_prompt and str(system_prompt).strip():
                # OpenClaw agent exec has no --append-system-prompt; prepend.
                prompt = f"{str(system_prompt).strip()}\n\n{prompt}"

            # Optional host-side seeds (Crabline Slack / smolclaw Gmail|Calendar)
            from agent_eval.openshell.crabline_seed import (
                load_case_annotations,
                seed_crabline_for_case,
            )
            from agent_eval.openshell.smolclaw_seed import seed_smolclaw_for_case

            case_annotations = load_case_annotations(config, case_id)
            sandbox_env_extra: dict[str, str] = {}
            try:
                seed_meta = seed_crabline_for_case(case_annotations)
            except Exception as e:
                logger.error("Crabline seed failed for %s: %s", case_id, e)
                raise
            if seed_meta:
                seed_path = case_output / "crabline-seed.json"
                seed_path.write_text(json.dumps(seed_meta, indent=2), encoding="utf-8")
                # Non-secret metadata for the agent (not the seeded text/code).
                sandbox_env_extra.update(
                    {
                        "CRABLINE_SEED_CHANNEL": str(seed_meta.get("channel") or ""),
                        "CRABLINE_SEED_TS": str(seed_meta.get("ts") or ""),
                        "CRABLINE_SEED_OLDEST": str(seed_meta.get("oldest_ts") or ""),
                        "CRABLINE_CASE_USER": str(
                            case_annotations.get("slack_user")
                            or (case_annotations.get("crabline_seed") or {}).get("users")
                            or ""
                        ),
                    }
                )
            else:
                slack_user = str(case_annotations.get("slack_user") or "")
                if slack_user:
                    sandbox_env_extra["CRABLINE_CASE_USER"] = slack_user

            try:
                smol_meta = seed_smolclaw_for_case(case_annotations)
            except Exception as e:
                logger.error("smolclaw seed failed for %s: %s", case_id, e)
                raise
            if smol_meta:
                (case_output / "smolclaw-seed.json").write_text(
                    json.dumps(smol_meta, indent=2), encoding="utf-8"
                )
                kind = str(smol_meta.get("kind") or "")
                if kind == "calendar" and smol_meta.get("event_id"):
                    sandbox_env_extra["SMOLCLAW_SEED_EVENT_ID"] = str(
                        smol_meta["event_id"]
                    )
                if kind == "gmail" and smol_meta.get("message_id"):
                    sandbox_env_extra["SMOLCLAW_SEED_MESSAGE_ID"] = str(
                        smol_meta["message_id"]
                    )
                    if smol_meta.get("thread_id"):
                        sandbox_env_extra["SMOLCLAW_SEED_THREAD_ID"] = str(
                            smol_meta["thread_id"]
                        )

            # Build env to forward to sandbox (API keys + config env)
            sandbox_env = _sandbox_env(config)
            sandbox_env.update({k: v for k, v in sandbox_env_extra.items() if v})

            # Build command based on runner type
            # Default depends on whether providers are configured (OpenClaw) or not (Claude Code)
            if hasattr(config.runner, 'type') and config.runner.type:
                runner_type = config.runner.type
            elif getattr(config.runner, 'providers', None):
                runner_type = "openclaw"  # Providers configured = OpenClaw
            else:
                runner_type = "claude-code"  # Default for simple cases
            if runner_type == "cli":
                # CLI runner: use command from config with {args}/{case_id}
                # substitution. Use /bin/sh (not bash) — Quay OpenClaw and
                # many minimal images do not ship bash (exit 127).
                # Case files upload to /sandbox/<case_id>/ (OpenShell nests dirs).
                cli_command = config.runner.command
                if cli_command:
                    if isinstance(cli_command, list):
                        cli_command = " ".join(cli_command)
                    cli_command = (
                        cli_command.replace("{args}", prompt)
                        .replace("{case_id}", case_id)
                    )
                    cmd = ["/bin/sh", "-c", cli_command]
                    stdin_data = None
                else:
                    raise ValueError("CLI runner requires 'command' in runner config")
            elif runner_type == "claude-code":
                # Claude Code runner
                cmd = [
                    "claude",
                    "--print",
                    "--output-format", "stream-json",
                    "--model", model,
                    "--max-turns", "1",  # Single turn for simple prompts
                ]
                # Add dangerously-skip-permissions for headless execution
                cmd.append("--dangerously-skip-permissions")
                stdin_data = prompt.encode()
            else:
                # OpenClaw runner (default)
                # Custom providers (e.g. inference.local) must be registered in
                # openclaw.json and passed via --config. --auth-env-only skips
                # config entirely (OpenClaw docs), so it cannot be used together
                # with models.providers — that is why Quay beta.7 reported
                # "Unknown model" for inference/claude-sonnet-4.
                providers = getattr(config.runner, 'providers', None)
                config_path = None
                auth_env_only = True
                await sandbox.exec(
                    name,
                    [
                        "mkdir",
                        "-p",
                        str(_OPENCLAW_STATE_DIR),
                        str(_OPENCLAW_TMP_DIR),
                    ],
                )
                sandbox_env["HOME"] = "/sandbox"
                sandbox_env["OPENCLAW_STATE_DIR"] = str(_OPENCLAW_STATE_DIR)
                sandbox_env["TMPDIR"] = str(_OPENCLAW_TMP_DIR)
                if providers:
                    openclaw_config: dict = {
                        "agents": {
                            "defaults": {
                                "model": {"primary": model},
                            }
                        },
                        "models": {
                            "mode": "merge",
                            "providers": {},
                        },
                    }
                    for provider_name, provider_cfg in providers.items():
                        provider_entry: dict = {
                            "baseUrl": provider_cfg.get("baseUrl", ""),
                            "apiKey": provider_cfg.get("apiKey", "empty"),
                            "api": provider_cfg.get("api", "openai-completions"),
                            "models": [],
                        }
                        for m in provider_cfg.get("models", []):
                            model_entry = {
                                "id": m.get("id", ""),
                                "name": m.get("name", m.get("id", "")),
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0,
                                },
                                "contextWindow": 200000,
                                "maxTokens": 8192,
                            }
                            if m.get("api"):
                                model_entry["api"] = m["api"]
                            provider_entry["models"].append(model_entry)
                        openclaw_config["models"]["providers"][provider_name] = (
                            provider_entry
                        )

                    config_path = Path("/sandbox/openclaw-eval.json")
                    config_json = json.dumps(openclaw_config)
                    # Quay OpenClaw image has node but not python3
                    await sandbox.exec(
                        name,
                        ["tee", str(config_path)],
                        stdin=config_json.encode(),
                    )
                    sandbox_env["OPENCLAW_CONFIG_PATH"] = str(config_path)
                    auth_env_only = False

                effort = config.runner.effort
                if not effort and config.runner.settings:
                    effort = config.runner.settings.get("effort")

                # Pass --state-dir so agent exec keeps SQLite (default temp state
                # is deleted on exit). Same path as OPENCLAW_STATE_DIR under
                # /sandbox (Landlock read_write). Needed for trajectory export.
                cmd = build_openclaw_argv(
                    model=model,
                    timeout_s=config.execution.timeout,
                    effort=effort,
                    cwd=Path("/sandbox"),
                    auth_env_only=auth_env_only,
                    config_path=config_path,
                    state_dir=_OPENCLAW_STATE_DIR,
                )
                # Prompt is positional argument in 'agent exec' format
                cmd.append(prompt)
                stdin_data = None

            logger.info(f"Executing case {case_id} in sandbox {name}")
            timeout = (config.execution.timeout or 600) + 60
            result = await sandbox.exec(
                name,
                cmd,
                workdir="/sandbox",
                stdin=stdin_data if runner_type != "cli" else None,
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
                        # OpenClaw prompt cases often never create /sandbox/output;
                        # AEH writes response.txt from the exec envelope on the host.
                        err = str(e)
                        if "No such file or directory" in err or "failed to resolve" in err:
                            logger.info(
                                "No sandbox %s to download for %s (ok for openclaw)",
                                output.path,
                                case_id,
                            )
                        else:
                            logger.warning(f"Failed to download {output.path}: {e}")

            # Parse output based on runner type
            if runner_type == "openclaw":
                case_result = parse_openclaw_to_case_dict(
                    result.stdout.encode(),
                    result.stderr.encode(),
                    result.return_code,
                    duration_s,
                )
                # Extract response text and write to output/ directory (following existing convention)
                response_text = case_result.get("response_text", "")
                output_dir = staged_case / "output"
                output_dir.mkdir(exist_ok=True)
                (output_dir / "response.txt").write_text(response_text)

                try:
                    events = await _harvest_openclaw_events(
                        sandbox,
                        name,
                        stdout_text=result.stdout,
                        prompt=prompt,
                        case_id=case_id,
                        case_output=case_output,
                        sandbox_env=sandbox_env,
                    )
                    with open(case_output / "events.json", "w") as f:
                        json.dump(events, f, indent=2)
                    logger.debug(
                        "Generated events.json for %s with %d events",
                        case_id,
                        len(events),
                    )
                except Exception as e:
                    logger.warning(f"Failed to generate events.json for {case_id}: {e}")
            else:
                # Generic result for cli/claude-code runners
                case_result = {
                    "exit_code": result.return_code,
                    "duration_s": round(duration_s, 1),
                    "token_usage": {"input": 0, "output": 0},
                    "cost_usd": None,
                    "num_turns": 1,
                    "response_text": result.stdout,
                    "stderr": result.stderr,
                }
                # Ensure judges/collect see a response even if sandbox
                # did not create outputs.path (e.g. CLI stdout-only).
                host_output = staged_case / "output"
                host_output.mkdir(exist_ok=True)
                response_file = host_output / "response.txt"
                if not response_file.exists() or not response_file.read_text().strip():
                    response_file.write_text(result.stdout or "")

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
