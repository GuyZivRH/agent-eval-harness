"""Tests for the OpenShell sandbox helper."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_eval.openshell.sandbox import ExecResult, OpenShellSandbox


def run_async(coro):
    """Helper to run async code in sync tests."""
    return asyncio.run(coro)


class TestOpenShellSandbox:
    """Tests for OpenShellSandbox class."""

    def test_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("OPENSHELL_GATEWAY_ENDPOINT", raising=False)
        monkeypatch.delenv("AGENT_EVAL_OPENSHELL_POLICY", raising=False)
        monkeypatch.delenv("AGENT_EVAL_OPENSHELL_PROVIDER", raising=False)

        sandbox = OpenShellSandbox.from_env()

        assert sandbox.gateway == "https://127.0.0.1:17670"
        assert sandbox.policy is None
        assert sandbox.provider is None

    def test_from_env_with_values(self, monkeypatch):
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "https://gateway.example.com:8080")
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_POLICY", "/path/to/policy.yaml")
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_PROVIDER", "anthropic")

        sandbox = OpenShellSandbox.from_env()

        assert sandbox.gateway == "https://gateway.example.com:8080"
        assert sandbox.policy == Path("/path/to/policy.yaml")
        assert sandbox.provider == "anthropic"

    def test_base_cmd(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://localhost:1234")
        cmd = sandbox._base_cmd()
        
        assert cmd == ["openshell", "--gateway-endpoint", "https://localhost:1234"]


class TestOpenShellSandboxCreate:
    """Tests for sandbox create operation."""

    def test_create_minimal(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                result = await sandbox.create("test-sandbox", "quay.io/org/image:v1")
            
            assert result == "test-sandbox"
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            
            assert "openshell" in cmd
            assert "--gateway-endpoint" in cmd
            assert "sandbox" in cmd
            assert "create" in cmd
            assert "--name" in cmd
            assert "test-sandbox" in cmd
            assert "--from" in cmd
            assert "quay.io/org/image:v1" in cmd
            assert "--no-tty" in cmd
            assert "--no-auto-providers" in cmd
            assert "--" in cmd
            assert "echo" in cmd
        
        run_async(_test())

    def test_create_with_policy(self):
        sandbox = OpenShellSandbox(
            gateway_endpoint="https://gw:1234",
            policy_file=Path("/etc/policy.yaml"),
        )
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                await sandbox.create("test", "image:v1")
            
            cmd = mock_run.call_args[0][0]
            assert "--policy" in cmd
            idx = cmd.index("--policy")
            assert cmd[idx + 1] == "/etc/policy.yaml"
        
        run_async(_test())

    def test_create_with_provider(self):
        sandbox = OpenShellSandbox(
            gateway_endpoint="https://gw:1234",
            provider="anthropic",
        )
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                await sandbox.create("test", "image:v1")
            
            cmd = mock_run.call_args[0][0]
            assert "--provider" in cmd
            idx = cmd.index("--provider")
            assert cmd[idx + 1] == "anthropic"
        
        run_async(_test())


class TestOpenShellSandboxUpload:
    """Tests for sandbox upload operation."""

    def test_upload(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                await sandbox.upload("my-sandbox", Path("/local/path"), "/remote/path")
            
            cmd = mock_run.call_args[0][0]
            assert "sandbox" in cmd
            assert "upload" in cmd
            assert "my-sandbox" in cmd
            assert "/local/path" in cmd
            assert "/remote/path" in cmd
        
        run_async(_test())


class TestOpenShellSandboxDownload:
    """Tests for sandbox download operation."""

    def test_download(self, tmp_path):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        local = tmp_path / "subdir" / "output"
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                await sandbox.download("my-sandbox", "/sandbox/output", local)
            
            cmd = mock_run.call_args[0][0]
            assert "sandbox" in cmd
            assert "download" in cmd
            assert "my-sandbox" in cmd
            assert "/sandbox/output" in cmd
            assert str(local) in cmd
            assert local.parent.exists()
        
        run_async(_test())


class TestOpenShellSandboxExec:
    """Tests for sandbox exec operation."""

    def test_exec_simple(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
            mock_proc.returncode = 0
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await sandbox.exec("my-sandbox", ["echo", "hello"])
            
            assert result.stdout == "output"
            assert result.stderr == ""
            assert result.return_code == 0
            
            call_args = mock_exec.call_args[0]
            assert "sandbox" in call_args
            assert "exec" in call_args
            assert "-n" in call_args
            assert "my-sandbox" in call_args
            assert "--workdir" in call_args
            assert "/sandbox" in call_args
            assert "--" in call_args
            assert "echo" in call_args
            assert "hello" in call_args
        
        run_async(_test())

    def test_exec_with_env(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await sandbox.exec(
                    "my-sandbox",
                    ["cmd"],
                    env={"KEY1": "val1", "KEY2": "val2"},
                )
            
            call_args = mock_exec.call_args[0]
            assert "--env" in call_args
            assert "KEY1=val1" in call_args
            assert "KEY2=val2" in call_args
        
        run_async(_test())

    def test_exec_with_stdin(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
            mock_proc.returncode = 0
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await sandbox.exec("my-sandbox", ["cat"], stdin=b"input data")
            
            mock_proc.communicate.assert_called_once()
            call_kwargs = mock_proc.communicate.call_args[1]
            assert call_kwargs["input"] == b"input data"
        
        run_async(_test())

    def test_exec_with_custom_workdir(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                await sandbox.exec("my-sandbox", ["cmd"], workdir="/custom/dir")
            
            call_args = mock_exec.call_args[0]
            idx = call_args.index("--workdir")
            assert call_args[idx + 1] == "/custom/dir"
        
        run_async(_test())

    def test_exec_timeout(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.kill = MagicMock()
            mock_proc.wait = AsyncMock()
            
            async def slow_communicate(input=None):
                await asyncio.sleep(10)
                return b"", b""
            
            mock_proc.communicate = slow_communicate
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await sandbox.exec(
                    "my-sandbox", ["sleep", "100"], timeout_s=0.1
                )
            
            assert result.return_code == 124
            assert result.stderr == "timeout"
            mock_proc.kill.assert_called_once()
        
        run_async(_test())

    def test_exec_nonzero_exit(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"error msg"))
            mock_proc.returncode = 1
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await sandbox.exec("my-sandbox", ["false"])
            
            assert result.return_code == 1
            assert result.stderr == "error msg"
        
        run_async(_test())


class TestOpenShellSandboxDelete:
    """Tests for sandbox delete operation."""

    def test_delete(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            with patch.object(sandbox, "_run", new_callable=AsyncMock) as mock_run:
                await sandbox.delete("my-sandbox")
            
            cmd = mock_run.call_args[0][0]
            assert "sandbox" in cmd
            assert "delete" in cmd
            assert "my-sandbox" in cmd
            mock_run.assert_called_with(cmd, check=False)
        
        run_async(_test())

    def test_delete_swallows_errors(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            with patch.object(
                sandbox, "_run", new_callable=AsyncMock, side_effect=RuntimeError("gone")
            ):
                await sandbox.delete("my-sandbox")
        
        run_async(_test())


class TestOpenShellSandboxRun:
    """Tests for internal _run method."""

    def test_run_success(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
            mock_proc.returncode = 0
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await sandbox._run(["openshell", "version"])
            
            assert result == "output"
        
        run_async(_test())

    def test_run_failure_raises(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
            mock_proc.returncode = 1
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                with pytest.raises(RuntimeError, match="OpenShell command failed"):
                    await sandbox._run(["openshell", "bad"])
        
        run_async(_test())

    def test_run_no_check(self):
        sandbox = OpenShellSandbox(gateway_endpoint="https://gw:1234")
        
        async def _test():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
            mock_proc.returncode = 1
            
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await sandbox._run(["openshell", "bad"], check=False)
            
            assert result == ""
        
        run_async(_test())


class TestExecResult:
    """Tests for ExecResult dataclass."""

    def test_exec_result_fields(self):
        result = ExecResult(stdout="out", stderr="err", return_code=42)
        
        assert result.stdout == "out"
        assert result.stderr == "err"
        assert result.return_code == 42
