"""OpenClaw CLI runner implementation.

OpenClaw is an open-source AI coding assistant. This runner invokes it via
`openclaw agent exec --json` for local evaluations. The shared helpers
(build_openclaw_argv, parse_openclaw_*) are also used by the OpenShell backend
for in-sandbox execution.
"""

import json
import logging
import os
import signal
import subprocess
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import EvalRunner, RunResult

logger = logging.getLogger(__name__)

# Valid effort levels for --thinking flag
OPENCLAW_EFFORTS = frozenset({"off", "minimal", "low", "medium", "high"})


def build_openclaw_argv(
    model: str,
    cwd: Optional[Path] = None,
    timeout_s: Optional[int] = None,
    effort: Optional[str] = None,
    auth_env_only: bool = True,
    state_dir: Optional[Path] = None,
) -> List[str]:
    """Build openclaw agent exec argv.

    Shared by OpenClawRunner (local) and openshell backend (in-sandbox).

    Args:
        model: Model identifier (e.g. "anthropic/claude-sonnet-4-6").
        cwd: Working directory for the agent.
        timeout_s: Timeout in seconds.
        effort: Thinking effort level (off|minimal|low|medium|high).
        auth_env_only: Read API keys from env only, not config file (CI-safe).
        state_dir: Directory to persist session state for trajectory capture.
            Without this, OpenClaw deletes ephemeral state on exit.

    Returns:
        Command argv list for subprocess.
    """
    cmd = ["openclaw", "agent", "exec", "--json"]
    if cwd:
        cmd.extend(["--cwd", str(cwd)])
    if model:
        cmd.extend(["--model", model])
    if timeout_s:
        cmd.extend(["--timeout", str(timeout_s)])
    if effort:
        cmd.extend(["--thinking", effort])
    if state_dir:
        cmd.extend(["--state-dir", str(state_dir)])
    if auth_env_only:
        cmd.append("--auth-env-only")
    cmd.extend(["--message-file", "-"])
    return cmd


def _parse_openclaw_envelope(
    stdout: bytes, stderr: bytes
) -> Tuple[Optional[dict], str]:
    """Parse OpenClaw JSON envelope.

    Returns:
        Tuple of (parsed_data, error_message). If JSON parsing fails,
        parsed_data is None and error_message contains stderr.
    """
    try:
        data = json.loads(stdout)
        error_msg = data.get("error", {}).get("message") or ""
        return data, stderr.decode(errors="replace") + error_msg
    except json.JSONDecodeError:
        return None, stderr.decode(errors="replace")


def parse_openclaw_to_run_result(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    duration_s: float,
) -> RunResult:
    """Parse OpenClaw JSON into RunResult (for local runner / EvalHub).

    Args:
        stdout: Raw stdout bytes from openclaw process.
        stderr: Raw stderr bytes from openclaw process.
        returncode: Process exit code.
        duration_s: Execution duration in seconds.

    Returns:
        RunResult with parsed metrics.
    """
    data, error_msg = _parse_openclaw_envelope(stdout, stderr)
    if data is None:
        return RunResult(
            exit_code=returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=error_msg,
            duration_s=round(duration_s, 1),
        )

    return RunResult(
        exit_code=returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=error_msg,
        duration_s=round(duration_s, 1),
        cost_usd=data.get("costUsd"),
        token_usage={
            "input": data.get("usage", {}).get("input", 0),
            "output": data.get("usage", {}).get("output", 0),
        },
        num_turns=data.get("assistantTurns"),
        resolved_model=data.get("model"),
        raw_output=data,
    )


def parse_openclaw_to_case_dict(
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    duration_s: float,
) -> dict:
    """Parse OpenClaw JSON into case_result dict (for openshell backend per_case).

    Args:
        stdout: Raw stdout bytes from openclaw process.
        stderr: Raw stderr bytes from openclaw process.
        returncode: Process exit code.
        duration_s: Execution duration in seconds.

    Returns:
        Dict with execution metrics for suite run_result.json.
    """
    data, error_msg = _parse_openclaw_envelope(stdout, stderr)
    if data is None:
        return {
            "exit_code": returncode,
            "duration_s": round(duration_s, 1),
            "token_usage": {"input": 0, "output": 0},
            "cost_usd": None,
            "num_turns": None,
            "stderr": error_msg,
        }

    return {
        "exit_code": returncode,
        "duration_s": round(duration_s, 1),
        "token_usage": {
            "input": data.get("usage", {}).get("input", 0),
            "output": data.get("usage", {}).get("output", 0),
        },
        "cost_usd": data.get("costUsd"),
        "num_turns": data.get("assistantTurns"),
        "resolved_model": data.get("model"),
        "stderr": error_msg,
    }


class OpenClawRunner(EvalRunner):
    """Run a skill or prompt with ``openclaw agent exec --json``.

    This runner is used for local evaluations (--runner local) and EvalHub.
    For OpenShell sandboxed execution, use --runner openshell instead.
    """

    @classmethod
    def from_config(cls, config, *, log_prefix=None, **overrides):
        """Construct runner from EvalConfig.

        Args:
            config: EvalConfig instance.
            log_prefix: Progress logging prefix.
            **overrides: Runner-specific overrides (e.g. effort).

        Returns:
            Configured OpenClawRunner instance.
        """
        env = {**config.execution.env, **config.runner.env}
        effort = overrides.get("effort", config.runner.effort)
        if effort and effort not in OPENCLAW_EFFORTS:
            raise ValueError(
                f"Invalid OpenClaw effort '{effort}'. "
                f"Must be one of: {sorted(OPENCLAW_EFFORTS)}"
            )
        return cls(
            effort=effort,
            env=env,
            log_prefix=log_prefix,
        )

    def __init__(
        self,
        effort: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        log_prefix: Optional[str] = None,
    ):
        """Initialize OpenClawRunner.

        Args:
            effort: Thinking effort level (off|minimal|low|medium|high).
            env: Environment variables to inject.
            log_prefix: Progress logging prefix.
        """
        self._effort = effort
        self._env = env or {}
        self._log_prefix = log_prefix

    @property
    def name(self) -> str:
        return "openclaw"

    def execute(
        self,
        target: Optional[str],
        args: str,
        workspace: Path,
        model: str,
        settings_path: Optional[Path] = None,
        system_prompt: Optional[str] = None,
        max_budget_usd: float = 5.0,
        timeout_s: int = 600,
        extra_env: Optional[dict] = None,
    ) -> RunResult:
        """Execute a skill or prompt via OpenClaw.

        Args:
            target: Skill name for skill mode, or None for prompt mode.
            args: Skill arguments or prompt text.
            workspace: Pre-staged workspace directory.
            model: Model identifier.
            settings_path: Unused (OpenClaw has no equivalent).
            system_prompt: System prompt (v1: logs warning and ignores).
            max_budget_usd: Budget limit (v1: logs warning if non-default).
            timeout_s: Timeout in seconds.
            extra_env: Additional environment variables.

        Returns:
            RunResult with execution metrics.
        """
        del settings_path

        if max_budget_usd not in (None, 5.0):
            warnings.warn(
                f"OpenClaw does not enforce max_budget_usd={max_budget_usd}; "
                "timeout is the available cap.",
                RuntimeWarning,
                stacklevel=2,
            )

        if system_prompt:
            warnings.warn(
                "OpenClaw runner ignores system_prompt in v1; "
                "prepend to prompt if needed.",
                RuntimeWarning,
                stacklevel=2,
            )

        workspace = workspace.resolve()

        cmd = build_openclaw_argv(
            model=model,
            cwd=workspace,
            timeout_s=timeout_s,
            effort=self._effort,
            auth_env_only=True,
        )

        if target:
            prompt = f"/{target} {args}" if args else f"/{target}"
        else:
            prompt = args or ""

        env = {**os.environ, **self._env, **(extra_env or {})}
        start_time = time.monotonic()

        popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workspace),
            env=env,
        )
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(
                input=prompt.encode(), timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass
            proc.wait()
            return RunResult(
                exit_code=2,
                stdout="",
                stderr=f"Timed out after {timeout_s}s",
                duration_s=timeout_s,
                resolved_model=model or None,
            )
        except BaseException:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass
            raise

        duration_s = time.monotonic() - start_time
        return parse_openclaw_to_run_result(stdout, stderr, proc.returncode, duration_s)
