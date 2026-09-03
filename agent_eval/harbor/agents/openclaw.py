"""Harbor OpenClaw agent (Forge / OpenShell parity).

Runs ``openclaw agent exec --json`` inside the Harbor trial container with:

* provider config pointing at a host-reachable OpenAI-compatible proxy
  (no OpenShell ``inference.local`` gateway)
* M365 Graph file-auth (OpenClaw 8.1 secret-env redaction workaround)
* retained ``--state-dir`` so trajectory export remains harvestable

Registered via Harbor ``-a`` import path::

    agent_eval.harbor.agents.openclaw:OpenClawAgent
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from agent_eval.agent.openclaw import (
    build_openclaw_argv,
    extract_openclaw_response,
    parse_openclaw_to_case_dict,
)
from agent_eval.openclaw.config import (
    build_openclaw_provider_config,
    dump_openclaw_provider_config,
    providers_from_env,
    resolve_openclaw_model_ref,
    stamp_config_env,
)
from agent_eval.openclaw.m365_auth import (
    apply_m365_file_auth_env,
    graph_curl_script,
    m365_header_body,
)
from agent_eval.openclaw.trajectory import (
    build_export_trajectory_argv,
    build_sessions_list_argv,
    trajectory_events_path,
)

logger = logging.getLogger(__name__)

_WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/workspace"))
# Prefer an image-local state dir: Harbor bind-mounts /workspace and OpenClaw
# 8.1 rejects state paths that are not mode 0700 / owner-only writable.
_STATE_DIR = Path(
    os.environ.get("OPENCLAW_STATE_DIR")
    or "/tmp/openclaw-aeh"
)
_TMP_DIR = _STATE_DIR / "tmp"
_CONFIG_PATH = _WORKSPACE / "openclaw-eval.json"
_HEADER_PATH = _TMP_DIR / "m365.header"
_CURL_PATH = _TMP_DIR / "graph-curl"
_OUTPUT_DIR = _WORKSPACE / "output"
_AGENT_STDOUT_LOG = "openclaw.txt"
_AGENT_TRAJECTORY_LOG = "openclaw-trajectory.jsonl"

try:
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
except ImportError:  # pragma: no cover
    BaseAgent = object  # type: ignore[misc, assignment]
    BaseEnvironment = Any  # type: ignore[misc, assignment]
    AgentContext = Any  # type: ignore[misc, assignment]


def _return_code(result: Any) -> int:
    for attr in ("return_code", "returncode", "exit_code", "code"):
        val = getattr(result, attr, None)
        if isinstance(val, int):
            return val
    return 1


def _stdout_of(result: Any) -> str:
    return getattr(result, "stdout", None) or ""


def _stderr_of(result: Any) -> str:
    return getattr(result, "stderr", None) or ""


class OpenClawAgent(BaseAgent):
    """AEH Harbor agent: OpenClaw ``agent exec`` + Forge M365 file-auth."""

    SUPPORTS_ATIF = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if BaseAgent is not object:
            super().__init__(*args, **kwargs)
        else:  # pragma: no cover
            self.logs_dir = Path(kwargs.get("logs_dir") or ".")
            self.model_name = kwargs.get("model_name")
            self.logger = logger
        self._trial_env: dict[str, str] = {}
        self._config_path: Optional[Path] = None
        self._model_ref: str = (
            getattr(self, "model_name", None) or "inference/claude-sonnet-4"
        )

    @staticmethod
    def name() -> str:
        return "openclaw"

    def version(self) -> str | None:
        return "aeh-1"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Install OpenClaw provider config + M365 file-auth into the trial."""
        # Only keep Forge/OpenClaw-related keys from the host agent process
        # (Harbor injects --agent-env secrets there). Copying all of os.environ
        # would later tempt us to forward host PATH/HOME into the container.
        keep_prefixes = ("M365_", "OPENCLAW_", "SLACK_", "FORGE_", "ANTHROPIC_", "OPENAI_")
        self._trial_env = {
            k: v for k, v in os.environ.items()
            if k.startswith(keep_prefixes) or k in {
                "PATH", "HOME", "TMPDIR", "TERM",
            }
        }
        env = self._trial_env

        # OpenClaw refuses state dirs that are group/world-writable or not
        # owned by the trial user (e.g. leftover root-owned mounts). Recreate
        # with 0700 ownership before writing auth/config files.
        state = shlex.quote(str(_STATE_DIR))
        tmp = shlex.quote(str(_TMP_DIR))
        out = shlex.quote(str(_OUTPUT_DIR))
        ws = shlex.quote(str(_WORKSPACE))
        # cwd="/" so we can create the Harbor workdir itself (podman exec -w
        # requires it to already exist). Use a fresh user-owned state dir —
        # OpenClaw 8.1 rejects group-writable / root-owned state paths.
        await environment.exec(
            "set -e; "
            f"rm -rf {state}; "
            f"mkdir -p {ws} {state} {tmp} {out}; "
            f"chmod 700 {state} {tmp}; "
            f"chmod 755 {ws} {out}",
            cwd="/",
        )

        providers = providers_from_env(env, model=self._model_ref)
        self._model_ref = resolve_openclaw_model_ref(self._model_ref, providers)
        if providers:
            config = build_openclaw_provider_config(self._model_ref, providers)
            await self._upload_text(
                environment,
                dump_openclaw_provider_config(config),
                str(_CONFIG_PATH),
            )
            stamp_config_env(self._trial_env, str(_CONFIG_PATH))
            self._config_path = _CONFIG_PATH
            self.logger.info(
                "Wrote OpenClaw provider config at %s (model=%s)",
                _CONFIG_PATH,
                self._model_ref,
            )

        token = apply_m365_file_auth_env(
            self._trial_env,
            header_path=str(_HEADER_PATH),
            curl_path=str(_CURL_PATH),
        )
        if token:
            await self._upload_text(
                environment,
                m365_header_body(token),
                str(_HEADER_PATH),
                mode="600",
            )
            await self._upload_text(
                environment,
                graph_curl_script(str(_HEADER_PATH)),
                str(_CURL_PATH),
                mode="755",
            )
            self.logger.info(
                "Installed M365 Graph auth header at %s (token scrubbed from env)",
                _HEADER_PATH,
            )

        probe = await environment.exec("command -v openclaw || true")
        if "openclaw" not in _stdout_of(probe):
            self.logger.warning(
                "openclaw binary not found on PATH in trial image; "
                "use deploy/harbor/Containerfile.openclaw"
            )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """Execute OpenClaw against the Harbor instruction and collect artifacts."""
        timeout_s: Optional[int] = None
        raw_timeout = os.environ.get("OPENCLAW_TIMEOUT") or os.environ.get(
            "AGENT_EVAL_OPENCLAW_TIMEOUT"
        )
        if raw_timeout:
            try:
                timeout_s = int(raw_timeout)
            except ValueError:
                timeout_s = None

        effort = os.environ.get("OPENCLAW_EFFORT") or os.environ.get(
            "AGENT_EVAL_OPENCLAW_EFFORT"
        )

        auth_env_only = self._config_path is None
        workspace = Path(
            self._trial_env.get("OPENCLAW_WORKSPACE")
            or os.environ.get("OPENCLAW_WORKSPACE")
            or str(_WORKSPACE)
        )
        # Do not pass --cwd to openclaw: under Harbor/Podman, OpenClaw's own
        # path resolve of --cwd has failed with "cannot resolve /workspace"
        # even when the directory exists. Inherit cwd via environment.exec.
        cmd = build_openclaw_argv(
            model=self._model_ref,
            cwd=None,
            timeout_s=timeout_s,
            effort=effort,
            auth_env_only=auth_env_only,
            state_dir=_STATE_DIR,
            config_path=self._config_path,
        )
        cmd.append(instruction)

        # Scrub secrets from the in-memory trial env (header files already
        # installed). Do NOT pass a giant env dict to environment.exec:
        # PodmanEnvironment forwards ``-e NAME`` value-free and reads values
        # from the Harbor host process, so dict values like HOME=/workspace
        # would be ignored and host HOME/PATH would leak into the trial.
        apply_m365_file_auth_env(
            self._trial_env,
            header_path=str(_HEADER_PATH),
            curl_path=str(_CURL_PATH),
        )

        # Inline path overrides in the shell so they apply inside the container.
        path_exports = " ".join(
            [
                f"HOME={shlex.quote(str(workspace))}",
                f"OPENCLAW_STATE_DIR={shlex.quote(str(_STATE_DIR))}",
                f"TMPDIR={shlex.quote(str(_TMP_DIR))}",
                f"M365_AUTH_HEADER_FILE={shlex.quote(str(_HEADER_PATH))}",
                f"M365_GRAPH_CURL={shlex.quote(str(_CURL_PATH))}",
            ]
        )
        shell_cmd = (
            f"export {path_exports}; "
            + " ".join(shlex.quote(part) for part in cmd)
        )
        self.logger.info("OpenClaw exec: %s", shell_cmd[:500])
        started = time.monotonic()
        ready = await environment.exec(
            "set -e; "
            f"mkdir -p {shlex.quote(str(workspace))} "
            f"{shlex.quote(str(workspace / 'output'))} "
            f"{shlex.quote(str(_STATE_DIR))} {shlex.quote(str(_TMP_DIR))}; "
            f"chmod 755 {shlex.quote(str(workspace))} || true; "
            f"chmod 700 {shlex.quote(str(_STATE_DIR))} "
            f"{shlex.quote(str(_TMP_DIR))} || true; "
            f"ls -ld {shlex.quote(str(workspace))} {shlex.quote(str(_STATE_DIR))}",
            cwd="/",
        )
        if _return_code(ready) != 0:
            raise RuntimeError(
                f"failed to prepare OpenClaw workdirs: "
                f"{_stderr_of(ready) or _stdout_of(ready)}"
            )
        self.logger.info("OpenClaw workdirs ready: %s", _stdout_of(ready).strip())
        result = await environment.exec(
            shell_cmd,
            cwd=str(workspace),
            timeout_sec=(timeout_s + 60) if timeout_s else None,
        )
        duration_s = time.monotonic() - started
        stdout = _stdout_of(result)
        stderr = _stderr_of(result)
        code = _return_code(result)

        await self._upload_text(
            environment,
            stdout + (("\n" + stderr) if stderr else ""),
            f"/logs/agent/{_AGENT_STDOUT_LOG}",
        )

        case = parse_openclaw_to_case_dict(
            stdout.encode(),
            stderr.encode(),
            code,
            duration_s,
        )
        response_text = (
            case.get("response_text")
            or extract_openclaw_response(stdout.encode())
            or stdout
        )
        await environment.exec(f"mkdir -p {shlex.quote(str(_OUTPUT_DIR))}")
        await self._upload_text(
            environment, response_text or "", str(_OUTPUT_DIR / "response.txt")
        )
        await self._upload_text(
            environment,
            json.dumps(case, indent=2),
            str(_OUTPUT_DIR / "raw.json"),
        )

        traj = await self._export_trajectory(environment, stdout, {
            "HOME": str(workspace),
            "OPENCLAW_STATE_DIR": str(_STATE_DIR),
            "TMPDIR": str(_TMP_DIR),
        })
        if traj:
            await self._upload_text(
                environment, traj, f"/logs/agent/{_AGENT_TRAJECTORY_LOG}"
            )
            await self._upload_text(
                environment,
                traj,
                str(_WORKSPACE / "openclaw-trajectory-events.jsonl"),
            )
            # Flat events.json for in-container score.py / used_m365 judges.
            try:
                from agent_eval.events import parse_openclaw_trajectory_events
                events = parse_openclaw_trajectory_events(traj)
                await self._upload_text(
                    environment,
                    json.dumps(events),
                    str(_WORKSPACE / "events.json"),
                )
            except Exception as exc:  # best-effort; judging still has trajectory
                logger.warning("Failed to write events.json from trajectory: %s", exc)

        for attr, value in (
            ("metadata", {"exit_code": code, "duration_s": duration_s}),
            ("n_input_tokens", (case.get("token_usage") or {}).get("input")),
            ("n_output_tokens", (case.get("token_usage") or {}).get("output")),
        ):
            if hasattr(context, attr) and value is not None:
                try:
                    setattr(context, attr, value)
                except Exception:
                    pass

        if code != 0:
            raise RuntimeError(
                f"openclaw agent exec exited {code}: {(stderr or stdout)[:800]}"
            )

    async def _export_trajectory(
        self,
        environment: BaseEnvironment,
        stdout_text: str,
        env: Mapping[str, str],
    ) -> str:
        """Best-effort ``sessions export-trajectory`` → events.jsonl text."""
        session_id = ""
        try:
            payload = json.loads(stdout_text[stdout_text.find("{") :])
            session_id = str(
                payload.get("sessionId") or payload.get("session_id") or ""
            )
        except Exception:
            session_id = ""
        if not session_id:
            return ""

        try:
            from agent_eval.events import (
                build_explicit_openclaw_session_key,
                resolve_openclaw_session_key_from_list,
            )

            session_key = build_explicit_openclaw_session_key(session_id)
        except Exception:
            session_key = session_id
            resolve_openclaw_session_key_from_list = None  # type: ignore[assignment]

        export_name = "aeh-harbor"
        argv = build_export_trajectory_argv(
            session_key,
            workspace=str(_WORKSPACE),
            output_name=export_name,
        )
        # Inline env: Podman ``-e NAME`` is value-free (host-process lookup).
        prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        result = await environment.exec(
            f"export {prefix}; " + " ".join(shlex.quote(a) for a in argv)
            if prefix else " ".join(shlex.quote(a) for a in argv),
            cwd=str(_WORKSPACE),
            timeout_sec=120,
        )
        if _return_code(result) != 0 and resolve_openclaw_session_key_from_list:
            listed = await environment.exec(
                f"export {prefix}; " + " ".join(shlex.quote(a) for a in build_sessions_list_argv())
                if prefix else " ".join(shlex.quote(a) for a in build_sessions_list_argv()),
                cwd=str(_WORKSPACE),
                timeout_sec=60,
            )
            alt = resolve_openclaw_session_key_from_list(
                _stdout_of(listed), session_id
            )
            if alt and alt != session_key:
                argv = build_export_trajectory_argv(
                    alt,
                    workspace=str(_WORKSPACE),
                    output_name=export_name,
                )
                result = await environment.exec(
                    f"export {prefix}; " + " ".join(shlex.quote(a) for a in argv)
                    if prefix else " ".join(shlex.quote(a) for a in argv),
                    cwd=str(_WORKSPACE),
                    timeout_sec=120,
                )

        if _return_code(result) != 0:
            return ""

        output_dir = None
        try:
            out = _stdout_of(result)
            summary = json.loads(out[out.find("{") :])
            output_dir = summary.get("outputDir")
        except Exception:
            output_dir = None
        events_path = trajectory_events_path(
            export_name, workspace=str(_WORKSPACE), output_dir=output_dir
        )
        cat = await environment.exec(f"cat {shlex.quote(events_path)} || true")
        return _stdout_of(cat)

    async def _upload_text(
        self,
        environment: BaseEnvironment,
        content: str,
        target_path: str,
        *,
        mode: Optional[str] = None,
    ) -> None:
        """Write text into the trial via a host temp file + ``upload_file``."""
        logs_dir = Path(getattr(self, "logs_dir", Path(".")))
        logs_dir.mkdir(parents=True, exist_ok=True)
        staging = logs_dir / f"upload-{Path(target_path).name}"
        staging.write_text(content, encoding="utf-8")
        parent = str(Path(target_path).parent)
        await environment.exec(f"mkdir -p {shlex.quote(parent)}")
        await environment.upload_file(staging, target_path)
        if mode:
            await environment.exec(f"chmod {mode} {shlex.quote(target_path)}")


OpenClawHarborAgent = OpenClawAgent
