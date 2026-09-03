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
            OPENSHELL_GATEWAY_ENDPOINT: Gateway URL (default: https://localhost:17670)
            AGENT_EVAL_OPENSHELL_POLICY: Path to policy YAML
            AGENT_EVAL_OPENSHELL_PROVIDER: Provider name for auth

        Returns:
            Configured OpenShellSandbox instance.
        """
        policy_path = os.environ.get("AGENT_EVAL_OPENSHELL_POLICY")
        return cls(
            gateway_endpoint=os.environ.get(
                "OPENSHELL_GATEWAY_ENDPOINT", "https://localhost:17670"
            ),
            policy_file=Path(policy_path) if policy_path else None,
            provider=os.environ.get("AGENT_EVAL_OPENSHELL_PROVIDER"),
        )

    def _base_cmd(self) -> List[str]:
        """Base command with gateway endpoint."""
        return ["openshell", "--gateway-endpoint", self.gateway]

    async def create(self, name: str, image: str) -> str:
        """Create sandbox and wait until Ready.

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
        ]
        if self.policy:
            cmd.extend(["--policy", str(self.policy)])
        if self.provider:
            cmd.extend(["--provider", self.provider])
        cmd.extend(["--", "echo", "sandbox ready"])
        await self._run(cmd)
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
        await self._run(cmd)

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
        await self._run(cmd)

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

    async def _run(self, cmd: List[str], check: bool = True) -> str:
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
            raise RuntimeError(
                f"OpenShell command failed: {' '.join(cmd)}\n{stderr.decode()}"
            )
        return stdout.decode()
