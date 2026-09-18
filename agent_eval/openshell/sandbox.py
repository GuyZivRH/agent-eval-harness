"""OpenShell sandbox lifecycle management.

Wraps the OpenShell CLI for creating, managing, and destroying sandboxes.
This is NOT a Harbor BaseEnvironment - it's a direct orchestration helper.
"""

import asyncio
import logging
import os
from asyncio.subprocess import PIPE
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Keep the sandbox alive while the runner performs the non-interactive SAW
# onboarding step after creation. Invoking start-agent as the main process
# makes provisioning fail because the image's onboarding path expects a TTY.
# The SAW agent image's launcher stages the Chief-of-Staff workspace and
# governed image skills before starting the OpenClaw gateway. Do not replace
# that entrypoint with `sleep infinity`, or the agent bundle never reaches the
# sandbox workspace.
CREATE_KEEPALIVE: List[str] = [
    "sh",
    "-c",
    "sh /opt/forge/start-governed-forwarders.sh > /tmp/forge-launcher.log 2>&1 || "
    "{ cat /tmp/forge-launcher.log >&2; sleep infinity; }",
]

# Quay OpenClaw lives under /opt/openclaw. Default OpenShell Landlock omits
# that tree, so exec of the ``openclaw`` shebang returns 126 (EACCES).
_BUNDLED_EVAL_POLICY = (
    Path(__file__).resolve().parents[2] / "deploy" / "openshell" / "eval-policy.yaml"
)


def bundled_eval_policy() -> Optional[Path]:
    """Return the repo eval-policy.yaml if present (allows /opt/openclaw)."""
    return _BUNDLED_EVAL_POLICY if _BUNDLED_EVAL_POLICY.is_file() else None


@dataclass
class ExecResult:
    """Result from sandbox exec (local to openshell module)."""

    stdout: str
    stderr: str
    return_code: int


class OpenShellSandbox:
    """Manages OpenShell sandbox lifecycle via CLI.

    This class wraps the `openshell` CLI to create sandboxes, upload/download
    files, execute commands, and clean up. It's designed for CI environments
    where TTY and interactive prompts are not available.

    Example:
        sandbox = OpenShellSandbox.from_env()
        name = await sandbox.create("eval-case-001", "quay.io/org/openclaw:v1")
        await sandbox.upload(name, workspace_dir, "/sandbox")
        result = await sandbox.exec(name, ["openclaw", "agent", "exec", ...])
        await sandbox.download(name, "/sandbox/output", output_dir)
        await sandbox.delete(name)
    """

    def __init__(
        self,
        gateway_endpoint: str,
        policy_file: Optional[Path] = None,
        provider: Optional[str] = None,
    ):
        """Initialize OpenShellSandbox.

        Args:
            gateway_endpoint: OpenShell gateway gRPC endpoint URL.
            policy_file: Path to OpenShell policy YAML file.
            provider: Provider name for model auth (e.g. "anthropic").
        """
        self.gateway = gateway_endpoint
        self.policy = policy_file
        self.provider = provider

    @classmethod
    def from_env(cls) -> "OpenShellSandbox":
        """Construct from environment variables.

        Environment variables:
            OPENSHELL_GATEWAY_ENDPOINT: Gateway URL (default: https://127.0.0.1:17670)
            OPENSHELL_GATEWAY_NAME: Optional registered gateway profile. When set,
                use the profile so the CLI loads its OIDC and mTLS credentials.
            AGENT_EVAL_OPENSHELL_POLICY: Path to policy YAML. When unset, uses
                ``deploy/openshell/eval-policy.yaml`` so Quay OpenClaw under
                ``/opt/openclaw`` is readable (otherwise ``openclaw`` exits 126).
            AGENT_EVAL_OPENSHELL_PROVIDER: Provider name for auth

        Returns:
            Configured OpenShellSandbox instance.
        """
        env_policy = os.environ.get("AGENT_EVAL_OPENSHELL_POLICY", "").strip()
        if env_policy:
            policy_file = Path(env_policy)
        else:
            policy_file = bundled_eval_policy()
            if policy_file is not None:
                logger.info("Using bundled OpenShell eval policy %s", policy_file)
        return cls(
            gateway_endpoint=os.environ.get(
                "OPENSHELL_GATEWAY_ENDPOINT", "https://127.0.0.1:17670"
            ),
            policy_file=policy_file,
            provider=os.environ.get("AGENT_EVAL_OPENSHELL_PROVIDER"),
        )

    def _base_cmd(self) -> List[str]:
        """Base command selecting the configured gateway profile or endpoint.

        A direct ``--gateway-endpoint`` bypasses the CLI gateway profile. That
        also bypasses the profile's mTLS bundle, which is required when the
        remote gateway requests a client certificate. Prefer the named profile
        in CI when one was registered; keep endpoint mode for local/default use.
        """
        gateway_name = os.environ.get("OPENSHELL_GATEWAY_NAME", "").strip()
        gateway_endpoint = self.gateway
        if "openshell-saw-agent-gateway.gz-forge-eval.svc.cluster.local" in gateway_endpoint:
            gateway_endpoint = "https://127.0.0.1:17671"
        # A namespace-local TLS bridge terminates the CLI connection on
        # localhost and presents the deployment client certificate upstream.
        # Do not let a stale named profile replace that endpoint.
        if gateway_name and gateway_endpoint == self.gateway and not gateway_endpoint.startswith(("https://127.0.0.1:", "http://127.0.0.1:")):
            return ["openshell", "-g", gateway_name]
        return ["openshell", "--gateway-endpoint", gateway_endpoint]

    async def create(self, name: str, image: str) -> str:
        """Create sandbox and wait until Ready.

        The create command uses ``--detach`` plus a long-lived keep-alive so the
        sandbox main process stays up. On Kubernetes (restartPolicy Never) a
        short-lived command such as ``echo`` makes the pod Failed.

        Args:
            name: Unique sandbox name.
            image: Container image with OpenClaw pre-installed.

        Returns:
            The sandbox name (same as input).

        Raises:
            RuntimeError: If sandbox creation fails.
        """
        cmd = self._base_cmd() + [
            "sandbox",
            "create",
            "--name",
            name,
            "--from",
            image,
            "--no-tty",
            "--no-auto-providers",
            "--detach",
        ]
        if self.policy:
            cmd.extend(["--policy", str(self.policy)])
        if self.provider:
            # The SAW agent image requires several capability providers in
            # addition to its model route. Accept a comma-separated value so
            # the pipeline can attach the complete deployment contract.
            for provider in (item.strip() for item in self.provider.split(",")):
                if provider:
                    cmd.extend(["--provider", provider])
        if CREATE_KEEPALIVE:
            cmd.extend(["--"] + CREATE_KEEPALIVE)
        logger.info(
            "OpenShell sandbox create requested name=%s gateway=%s provider=%s image=%s policy=%s",
            name,
            self.gateway,
            self.provider or "<none>",
            image,
            self.policy or "<none>",
        )
        await self._run(cmd, operation=f"sandbox create name={name}")
        return name

    async def upload(self, name: str, local: Path, remote: str) -> None:
        """Upload file or directory to sandbox.

        OpenShell dereferences symlinks via tar internally.

        Args:
            name: Sandbox name.
            local: Local path to upload.
            remote: Remote path in sandbox.

        Raises:
            RuntimeError: If upload fails.
        """
        cmd = self._base_cmd() + ["sandbox", "upload", name, str(local), remote]
        await self._run(cmd, operation=f"sandbox upload name={name} remote={remote}")

    async def download(self, name: str, remote: str, local: Path) -> None:
        """Download file or directory from sandbox.

        Args:
            name: Sandbox name.
            remote: Remote path in sandbox.
            local: Local path to download to.

        Raises:
            RuntimeError: If download fails.
        """
        local.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd() + ["sandbox", "download", name, remote, str(local)]
        await self._run(cmd, operation=f"sandbox download name={name} remote={remote}")

    async def exec(
        self,
        name: str,
        command: List[str],
        workdir: str = "/sandbox",
        stdin: Optional[bytes] = None,
        env: Optional[Dict[str, str]] = None,
        timeout_s: Optional[int] = None,
    ) -> ExecResult:
        """Execute command in sandbox.

        Args:
            name: Sandbox name.
            command: Command to execute.
            workdir: Working directory in sandbox.
            stdin: Input to pipe to command.
            env: Environment variables to set (via --env KEY=VALUE).
            timeout_s: Timeout in seconds.

        Returns:
            ExecResult with stdout, stderr, and return code.
        """
        cmd = self._base_cmd() + [
            "sandbox",
            "exec",
            "-n",
            name,
            "--workdir",
            workdir,
        ]
        if env:
            for key, value in env.items():
                cmd.extend(["--env", f"{key}={value}"])
        cmd.extend(["--"] + command)

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=PIPE if stdin else None, stdout=PIPE, stderr=PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(stdout="", stderr="timeout", return_code=124)

        return ExecResult(
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            return_code=proc.returncode or 0,
        )

    async def delete(self, name: str) -> None:
        """Delete sandbox.

        This method swallows errors (sandbox may already be gone).

        Args:
            name: Sandbox name.
        """
        cmd = self._base_cmd() + ["sandbox", "delete", name]
        try:
            await self._run(cmd, check=False)
        except Exception as e:
            logger.debug(f"Sandbox delete failed (may already be gone): {e}")

    async def _run(
        self, cmd: List[str], check: bool = True, operation: str = "openshell command"
    ) -> str:
        """Run OpenShell CLI command.

        Args:
            cmd: Command to run.
            check: Raise on non-zero exit code.

        Returns:
            stdout output.

        Raises:
            RuntimeError: If check=True and command fails.
        """
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        stdout, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            logger.error(
                "OpenShell operation failed operation=%s rc=%s stdout=%s stderr=%s",
                operation,
                proc.returncode,
                stdout.decode(errors="replace")[-2000:],
                stderr.decode(errors="replace")[-4000:],
            )
            raise RuntimeError(
                f"OpenShell command failed: {' '.join(cmd)}\n{stderr.decode()}"
            )
        return stdout.decode()
