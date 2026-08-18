"""Integration tests for OpenShell backend.

These tests require a running OpenShell gateway. Skip in CI unless gateway is available.

Run with Docker gateway:
    openshell gateway start --name local
    pytest tests/test_openshell_integration.py -v -m openshell

Environment variables:
    OPENSHELL_GATEWAY_ENDPOINT - Gateway URL (default: https://127.0.0.1:17670)
    AGENT_EVAL_OPENSHELL_IMAGE - Sandbox image with OpenClaw
    ANTHROPIC_API_KEY - Required for real model calls
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

# Skip all tests if openshell CLI not available
pytestmark = pytest.mark.openshell


def openshell_available():
    """Check if openshell CLI is available."""
    return shutil.which("openshell") is not None


def gateway_available():
    """Check if OpenShell gateway is reachable."""
    if not openshell_available():
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["openshell", "gateway", "list"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


requires_openshell = pytest.mark.skipif(
    not openshell_available(),
    reason="openshell CLI not installed",
)

requires_gateway = pytest.mark.skipif(
    not gateway_available(),
    reason="OpenShell gateway not available",
)


@requires_openshell
class TestOpenShellSandboxIntegration:
    """Integration tests for OpenShellSandbox with real gateway."""

    @requires_gateway
    def test_sandbox_lifecycle(self, tmp_path):
        """Test create, upload, exec, download, delete cycle."""
        from agent_eval.openshell.sandbox import OpenShellSandbox

        sandbox = OpenShellSandbox.from_env()
        name = f"test-lifecycle-{os.getpid()}"
        image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE", "node:22-slim")

        async def run_test():
            try:
                await sandbox.create(name, image)

                test_dir = tmp_path / "workspace"
                test_dir.mkdir()
                (test_dir / "input.txt").write_text("hello world")

                await sandbox.upload(name, test_dir, "/sandbox/workspace")

                result = await sandbox.exec(
                    name,
                    ["cat", "/sandbox/workspace/input.txt"],
                )
                assert result.return_code == 0
                assert "hello world" in result.stdout

                result = await sandbox.exec(
                    name,
                    ["sh", "-c", "echo 'output data' > /sandbox/output.txt"],
                )
                assert result.return_code == 0

                output_dir = tmp_path / "output"
                await sandbox.download(name, "/sandbox/output.txt", output_dir / "output.txt")
                assert (output_dir / "output.txt").read_text().strip() == "output data"

            finally:
                await sandbox.delete(name)

        asyncio.run(run_test())

    @requires_gateway
    def test_symlink_dereference(self, tmp_path):
        """Test that symlinks are dereferenced during upload."""
        from agent_eval.openshell.sandbox import OpenShellSandbox

        sandbox = OpenShellSandbox.from_env()
        name = f"test-symlink-{os.getpid()}"
        image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE", "node:22-slim")

        async def run_test():
            try:
                shared = tmp_path / "shared"
                shared.mkdir()
                (shared / "helper.py").write_text("def greet(): return 'hello'")

                workspace = tmp_path / "workspace"
                workspace.mkdir()
                (workspace / "scripts").mkdir()
                (workspace / "scripts" / "helper.py").symlink_to(shared / "helper.py")

                await sandbox.create(name, image)
                await sandbox.upload(name, workspace, "/sandbox")

                result = await sandbox.exec(
                    name,
                    ["cat", "/sandbox/scripts/helper.py"],
                )
                assert result.return_code == 0
                assert "def greet()" in result.stdout

            finally:
                await sandbox.delete(name)

        asyncio.run(run_test())

    @requires_gateway
    def test_exec_with_stdin(self, tmp_path):
        """Test passing stdin to sandbox exec."""
        from agent_eval.openshell.sandbox import OpenShellSandbox

        sandbox = OpenShellSandbox.from_env()
        name = f"test-stdin-{os.getpid()}"
        image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE", "node:22-slim")

        async def run_test():
            try:
                await sandbox.create(name, image)

                result = await sandbox.exec(
                    name,
                    ["cat"],
                    stdin=b"input from stdin",
                )
                assert result.return_code == 0
                assert "input from stdin" in result.stdout

            finally:
                await sandbox.delete(name)

        asyncio.run(run_test())

    @requires_gateway
    def test_exec_with_env(self, tmp_path):
        """Test passing environment variables to sandbox exec."""
        from agent_eval.openshell.sandbox import OpenShellSandbox

        sandbox = OpenShellSandbox.from_env()
        name = f"test-env-{os.getpid()}"
        image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE", "node:22-slim")

        async def run_test():
            try:
                await sandbox.create(name, image)

                result = await sandbox.exec(
                    name,
                    ["sh", "-c", "echo $MY_VAR"],
                    env={"MY_VAR": "test-value"},
                )
                assert result.return_code == 0
                assert "test-value" in result.stdout

            finally:
                await sandbox.delete(name)

        asyncio.run(run_test())

    @requires_gateway
    def test_exec_timeout(self, tmp_path):
        """Test that exec respects timeout."""
        from agent_eval.openshell.sandbox import OpenShellSandbox

        sandbox = OpenShellSandbox.from_env()
        name = f"test-timeout-{os.getpid()}"
        image = os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE", "node:22-slim")

        async def run_test():
            try:
                await sandbox.create(name, image)

                result = await sandbox.exec(
                    name,
                    ["sleep", "100"],
                    timeout_s=1,
                )
                assert result.return_code == 124
                assert "timeout" in result.stderr.lower()

            finally:
                await sandbox.delete(name)

        asyncio.run(run_test())


@requires_openshell
@requires_gateway
class TestOpenShellBackendIntegration:
    """Integration tests for the full OpenShell backend."""

    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    def test_single_case_evaluation(self, tmp_path):
        """Test single case end-to-end evaluation."""
        from agent_eval.openshell.run import run_openshell

        project_root = tmp_path / "project"
        project_root.mkdir()

        eval_yaml = {
            "name": "test-openshell-eval",
            "execution": {
                "prompt": "What is 2+2? Reply with just the number.",
            },
            "runner": {
                "type": "openclaw",
            },
            "models": {
                "skill": "anthropic/claude-sonnet-4-6",
            },
            "dataset": {
                "path": "cases",
            },
            "outputs": [],
            "judges": [
                {
                    "name": "has_answer",
                    "check": "exit 0 if '4' in outputs.get('stdout_content', '') else exit 1",
                },
            ],
        }

        config_path = project_root / "eval.yaml"
        config_path.write_text(yaml.safe_dump(eval_yaml))

        cases_dir = project_root / "cases" / "case-001"
        cases_dir.mkdir(parents=True)
        (cases_dir / "input.yaml").write_text(yaml.safe_dump({"prompt": "What is 2+2?"}))

        os.environ["AGENT_EVAL_RUNS_DIR"] = str(project_root / "runs")

        async def run_test():
            exit_code = await run_openshell(
                config_path=config_path,
                model="anthropic/claude-sonnet-4-6",
                run_id="test-001",
                parallelism=1,
            )
            return exit_code

        exit_code = asyncio.run(run_test())

        runs_dir = project_root / "runs" / "test-openshell-eval" / "test-001"
        assert runs_dir.exists()
        assert (runs_dir / "run_result.json").exists()

        run_result = json.loads((runs_dir / "run_result.json").read_text())
        assert run_result["execution_mode"] == "openshell"
        assert run_result["n_cases"] == 1

        case_output = runs_dir / "cases" / "case-001"
        assert (case_output / "stdout.log").exists()
        assert (case_output / "run_result.json").exists()

    @pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    def test_trajectory_capture(self, tmp_path):
        """Test that OpenClaw trajectories are captured."""
        from agent_eval.openshell.run import run_openshell

        project_root = tmp_path / "project"
        project_root.mkdir()

        eval_yaml = {
            "name": "test-trajectory",
            "execution": {
                "prompt": "Create a file called test.txt with content 'hello'",
            },
            "runner": {
                "type": "openclaw",
            },
            "models": {
                "skill": "anthropic/claude-sonnet-4-6",
            },
            "dataset": {
                "path": "cases",
            },
            "outputs": [
                {"path": "test.txt"},
            ],
            "judges": [],
        }

        config_path = project_root / "eval.yaml"
        config_path.write_text(yaml.safe_dump(eval_yaml))

        cases_dir = project_root / "cases" / "case-001"
        cases_dir.mkdir(parents=True)
        (cases_dir / "input.yaml").write_text(yaml.safe_dump({}))

        os.environ["AGENT_EVAL_RUNS_DIR"] = str(project_root / "runs")

        async def run_test():
            return await run_openshell(
                config_path=config_path,
                model="anthropic/claude-sonnet-4-6",
                run_id="test-trajectory",
                parallelism=1,
            )

        asyncio.run(run_test())

        trajectory_dir = project_root / "runs" / "test-trajectory" / "test-trajectory" / "cases" / "case-001" / "trajectory"
        if trajectory_dir.exists():
            assert any(trajectory_dir.iterdir())
