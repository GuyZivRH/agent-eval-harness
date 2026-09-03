"""Tests for the OpenShell backend orchestrator."""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import agent_eval._bootstrap
from agent_eval.openshell.run import (
    _apply_m365_file_auth_env,
    _child_env,
    _resolve_prompt,
    _sandbox_env,
)


class TestChildEnv:
    """Tests for _child_env (bootstrap sentinel stripping)."""

    def test_strips_bootstrap_sentinel(self, monkeypatch):
        sentinel = agent_eval._bootstrap._SENTINEL
        monkeypatch.setenv(sentinel, "1")
        
        env = _child_env()
        
        assert sentinel not in env

    def test_strips_sentinel_after_extras(self, monkeypatch):
        sentinel = agent_eval._bootstrap._SENTINEL
        monkeypatch.setenv(sentinel, "1")
        
        # Even if extra tries to reinstate it, it's stripped
        env = _child_env({sentinel: "should-be-stripped"})
        
        assert sentinel not in env

    def test_preserves_other_env_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("PATH", "/usr/bin")
        
        env = _child_env()
        
        assert env["ANTHROPIC_API_KEY"] == "test-key"
        assert "PATH" in env

    def test_includes_extra_vars(self, monkeypatch):
        env = _child_env({"CUSTOM_VAR": "custom-value"})
        
        assert env["CUSTOM_VAR"] == "custom-value"

    def test_extra_overrides_existing(self, monkeypatch):
        monkeypatch.setenv("EXISTING", "original")
        
        env = _child_env({"EXISTING": "overridden"})
        
        assert env["EXISTING"] == "overridden"


class TestSandboxEnv:
    """Tests for _sandbox_env (API key forwarding)."""

    def test_forwards_api_keys(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-123")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-456")
        
        config = _mock_config()
        env = _sandbox_env(config)
        
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-123"
        assert env["OPENAI_API_KEY"] == "sk-openai-456"

    def test_forwards_provider_config(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://custom.api")
        
        config = _mock_config()
        env = _sandbox_env(config)
        
        assert env["ANTHROPIC_MODEL"] == "claude-3"
        assert env["ANTHROPIC_BASE_URL"] == "https://custom.api"

    def test_merges_execution_env(self, monkeypatch):
        monkeypatch.setenv("SOURCE_VAR", "resolved-value")
        
        config = _mock_config(
            execution_env={"CUSTOM": "direct", "RESOLVED": "$SOURCE_VAR"}
        )
        env = _sandbox_env(config)
        
        assert env["CUSTOM"] == "direct"
        assert env["RESOLVED"] == "resolved-value"

    def test_merges_runner_env(self, monkeypatch):
        config = _mock_config(runner_env={"RUNNER_VAR": "runner-value"})
        env = _sandbox_env(config)
        
        assert env["RUNNER_VAR"] == "runner-value"

    def test_runner_env_overrides_execution_env(self, monkeypatch):
        config = _mock_config(
            execution_env={"SHARED": "from-execution"},
            runner_env={"SHARED": "from-runner"},
        )
        env = _sandbox_env(config)
        
        assert env["SHARED"] == "from-runner"

    def test_skips_none_values(self, monkeypatch):
        config = _mock_config(execution_env={"NULL_VAR": None})
        env = _sandbox_env(config)
        
        assert "NULL_VAR" not in env

    def test_skips_unresolved_refs(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        
        config = _mock_config(execution_env={"UNRESOLVED": "$MISSING_VAR"})
        env = _sandbox_env(config)
        
        assert "UNRESOLVED" not in env


class TestApplyM365FileAuthEnv:
    """OpenClaw 8.1-safe Graph auth: header file env, drop secret-named token."""

    def test_promotes_token_to_header_file_env(self):
        env = {
            "M365_ACCESS_TOKEN": "eyJhbGciOiJSUzI1NiJ9.aaa.bbb",
            "M365_USER": "demo@example.com",
            "M365_CLIENT_SECRET": "shh",
        }
        token = _apply_m365_file_auth_env(env)
        assert token == "eyJhbGciOiJSUzI1NiJ9.aaa.bbb"
        assert "M365_ACCESS_TOKEN" not in env
        assert "M365_CLIENT_SECRET" not in env
        assert env["M365_USER"] == "demo@example.com"
        assert env["M365_AUTH_HEADER_FILE"].endswith("m365.header")
        assert env["M365_GRAPH_CURL"].endswith("graph-curl")

    def test_noop_without_token(self):
        env = {"M365_USER": "demo@example.com"}
        assert _apply_m365_file_auth_env(env) is None
        assert env == {"M365_USER": "demo@example.com"}


class TestResolvePrompt:
    """Tests for _resolve_prompt (template resolution)."""

    def test_literal_prompt(self):
        config = _mock_config(prompt="What is 2+2?")
        result = _resolve_prompt(config, {})
        
        assert result == "What is 2+2?"

    def test_simple_format_template(self):
        config = _mock_config(prompt="Process {ticket_id}")
        result = _resolve_prompt(config, {"ticket_id": "JIRA-123"})
        
        assert result == "Process JIRA-123"

    def test_optional_field_present(self):
        config = _mock_config(prompt="Do {task} with {option?}")
        result = _resolve_prompt(config, {"task": "work", "option": "extra"})
        
        assert result == "Do work with extra"

    def test_optional_field_missing(self):
        config = _mock_config(prompt="Do {task} with {option?}")
        result = _resolve_prompt(config, {"task": "work"})
        
        assert result == "Do work with"

    def test_missing_required_field_raises(self):
        config = _mock_config(prompt="Process {required_field}")
        
        with pytest.raises(ValueError, match="Missing required field"):
            _resolve_prompt(config, {})

    def test_jinja_template(self):
        config = _mock_config(prompt="{{ input.prompt }}")
        result = _resolve_prompt(config, {"prompt": "Hello from Jinja"})
        
        assert result == "Hello from Jinja"

    def test_jinja_with_default_filter(self):
        config = _mock_config(prompt="{{ input.name | default('Anonymous') }}")
        result = _resolve_prompt(config, {})
        
        assert result == "Anonymous"

    def test_jinja_missing_required_raises(self):
        config = _mock_config(prompt="{{ input.missing_field }}")
        
        with pytest.raises(ValueError, match="Undefined variable"):
            _resolve_prompt(config, {})

    def test_fallback_to_input_prompt(self):
        config = _mock_config(prompt=None, arguments=None)
        result = _resolve_prompt(config, {"prompt": "Fallback prompt"})
        
        assert result == "Fallback prompt"

    def test_empty_when_no_template_or_input(self):
        config = _mock_config(prompt=None, arguments=None)
        result = _resolve_prompt(config, {})
        
        assert result == ""

    def test_uses_arguments_if_no_prompt(self):
        config = _mock_config(prompt=None, arguments="Run {task}")
        result = _resolve_prompt(config, {"task": "eval"})
        
        assert result == "Run eval"


class TestRunCaseEnvForwarding:
    """Tests verifying _run_case passes env to sandbox.exec()."""

    def test_run_case_passes_env_to_exec(self, tmp_path):
        """Verify sandbox.exec receives forwarded env vars."""
        from agent_eval.openshell.run import _run_case
        from agent_eval.openshell.sandbox import OpenShellSandbox
        import asyncio
        
        # Set up env vars that should be forwarded
        os.environ["ANTHROPIC_API_KEY"] = "test-api-key"
        
        try:
            config = _mock_config(prompt="test")
            
            # Create staged case directory
            staged_case = tmp_path / "cases" / "case-001"
            staged_case.mkdir(parents=True)
            (staged_case / "input.yaml").write_text(yaml.safe_dump({}))
            
            output_dir = tmp_path / "output"
            output_dir.mkdir()
            
            # Mock sandbox
            sandbox = MagicMock(spec=OpenShellSandbox)
            sandbox.create = AsyncMock()
            sandbox.upload = AsyncMock()
            sandbox.download = AsyncMock()
            sandbox.delete = AsyncMock()
            
            exec_result = MagicMock()
            exec_result.stdout = json.dumps({"ok": True})
            exec_result.stderr = ""
            exec_result.return_code = 0
            sandbox.exec = AsyncMock(return_value=exec_result)
            
            async def run_test():
                sem = asyncio.Semaphore(1)
                await _run_case(
                    sandbox, config, staged_case, "model", "image:v1",
                    output_dir, sem, keep=False,
                )
            
            asyncio.run(run_test())
            
            # Verify exec was called with env
            sandbox.exec.assert_called()
            call_kwargs = sandbox.exec.call_args[1]
            assert "env" in call_kwargs
            assert call_kwargs["env"]["ANTHROPIC_API_KEY"] == "test-api-key"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


class TestRunCasePromptResolution:
    """Tests verifying _run_case resolves prompt templates."""

    def test_run_case_resolves_jinja_prompt(self, tmp_path):
        """Verify Jinja templates are resolved before sending to sandbox."""
        from agent_eval.openshell.run import _run_case
        from agent_eval.openshell.sandbox import OpenShellSandbox
        import asyncio
        
        config = _mock_config(prompt="{{ input.message }}")
        
        staged_case = tmp_path / "cases" / "case-001"
        staged_case.mkdir(parents=True)
        (staged_case / "input.yaml").write_text(
            yaml.safe_dump({"message": "Hello resolved"})
        )
        
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        sandbox = MagicMock(spec=OpenShellSandbox)
        sandbox.create = AsyncMock()
        sandbox.upload = AsyncMock()
        sandbox.download = AsyncMock()
        sandbox.delete = AsyncMock()
        
        exec_result = MagicMock()
        exec_result.stdout = json.dumps({"ok": True})
        exec_result.stderr = ""
        exec_result.return_code = 0
        sandbox.exec = AsyncMock(return_value=exec_result)
        
        async def run_test():
            sem = asyncio.Semaphore(1)
            await _run_case(
                sandbox, config, staged_case, "model", "image:v1",
                output_dir, sem, keep=False,
            )
        
        asyncio.run(run_test())
        
        # Verify stdin contains resolved prompt
        call_kwargs = sandbox.exec.call_args[1]
        stdin_data = call_kwargs.get("stdin", b"")
        assert b"Hello resolved" in stdin_data


def _mock_config(
    prompt=None,
    arguments=None,
    execution_env=None,
    runner_env=None,
):
    """Create a mock EvalConfig for testing."""
    config = MagicMock()
    config.execution.prompt = prompt
    config.execution.arguments = arguments
    config.execution.timeout = 300
    config.execution.env = execution_env or {}
    config.runner.effort = None
    config.runner.settings = {}
    config.runner.env = runner_env or {}
    config.outputs = []
    return config
