"""Unit tests for shared OpenClaw helpers + Harbor OpenClaw wiring."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import pytest

from agent_eval.openclaw.config import (
    build_openclaw_provider_config,
    providers_from_env,
    resolve_openclaw_model_ref,
    stamp_config_env,
)
from agent_eval.openclaw.m365_auth import (
    apply_m365_file_auth_env,
    graph_curl_script,
    m365_header_body,
    write_m365_graph_auth_files,
)
from agent_eval.openclaw.trajectory import (
    build_export_trajectory_argv,
    trajectory_events_path,
)


def test_apply_m365_file_auth_env_scrubs_secrets():
    env = {
        "M365_ACCESS_TOKEN": "tok-secret",
        "M365_CLIENT_SECRET": "client-secret",
        "M365_USER": "demo@example.com",
    }
    token = apply_m365_file_auth_env(
        env, header_path="/tmp/h", curl_path="/tmp/c"
    )
    assert token == "tok-secret"
    assert "M365_ACCESS_TOKEN" not in env
    assert "M365_CLIENT_SECRET" not in env
    assert env["M365_AUTH_HEADER_FILE"] == "/tmp/h"
    assert env["M365_GRAPH_CURL"] == "/tmp/c"
    assert env["M365_USER"] == "demo@example.com"


def test_apply_m365_file_auth_env_noop_without_token():
    env = {"M365_USER": "x"}
    assert apply_m365_file_auth_env(env) is None
    assert env == {"M365_USER": "x"}


def test_m365_header_and_curl_helpers():
    body = m365_header_body("abc")
    assert body == "Authorization: Bearer abc\n"
    script = graph_curl_script("/workspace/.openclaw/tmp/m365.header")
    assert "#!/bin/sh" in script
    assert '@"/workspace/.openclaw/tmp/m365.header"' in script


def test_write_m365_graph_auth_files(tmp_path: Path):
    env = {"M365_ACCESS_TOKEN": "tok"}
    header = tmp_path / "m365.header"
    curl = tmp_path / "graph-curl"
    assert write_m365_graph_auth_files(
        env, header_path=header, curl_path=curl
    )
    assert header.read_text() == "Authorization: Bearer tok\n"
    assert "curl" in curl.read_text()
    assert "M365_ACCESS_TOKEN" not in env


def test_build_openclaw_provider_config():
    providers = {
        "inference": {
            "baseUrl": "http://host.containers.internal:8000/v1",
            "apiKey": "empty",
            "api": "openai-completions",
            "models": [{"id": "claude-sonnet-4", "name": "Claude Sonnet 4"}],
        }
    }
    cfg = build_openclaw_provider_config("inference/claude-sonnet-4", providers)
    assert cfg["agents"]["defaults"]["model"]["primary"] == "inference/claude-sonnet-4"
    entry = cfg["models"]["providers"]["inference"]
    assert entry["baseUrl"].startswith("http://host.containers.internal")
    assert entry["models"][0]["id"] == "claude-sonnet-4"


def test_providers_from_env_and_model_ref():
    env = {
        "OPENCLAW_INFERENCE_BASE_URL": "http://127.0.0.1:8000/v1",
        "OPENCLAW_INFERENCE_API_KEY": "k",
        "OPENCLAW_INFERENCE_MODEL": "claude-sonnet-4",
    }
    providers = providers_from_env(env, model="claude-sonnet-4")
    assert providers is not None
    assert "inference" in providers
    assert resolve_openclaw_model_ref("claude-sonnet-4", providers) == (
        "inference/claude-sonnet-4"
    )
    stamped: dict[str, str] = {}
    stamp_config_env(stamped, "/workspace/openclaw-eval.json")
    assert stamped["OPENCLAW_CONFIG_PATH"] == "/workspace/openclaw-eval.json"


def test_trajectory_helpers():
    argv = build_export_trajectory_argv("agent:main:sess", output_name="aeh")
    assert argv[:3] == ["openclaw", "sessions", "export-trajectory"]
    assert "--session-key" in argv
    path = trajectory_events_path("aeh", workspace="/workspace")
    assert path.endswith("/aeh/events.jsonl")


def test_harbor_runner_maps_openclaw_to_import_path():
    from agent_eval.harbor import run as harbor_run

    mapped = harbor_run._RUNNER_TO_HARBOR_AGENT.get("openclaw")
    assert mapped == "agent_eval.harbor.agents.openclaw:OpenClawAgent"
    assert ":" in mapped


def test_harbor_cmd_uses_agent_import_path_for_openclaw(monkeypatch, tmp_path):
    """Ensure import-path agents are passed via ``-a module:Class``."""
    from agent_eval.harbor import run as harbor_run

    agent_name = harbor_run._RUNNER_TO_HARBOR_AGENT["openclaw"]
    assert ":" in agent_name
    # Harbor 0.22 accepts custom import paths on ``-a`` (same as stock names).
    cmd = ["harbor", "run", "-m", "m", "-a", agent_name]
    assert cmd[cmd.index("-a") + 1] == "agent_eval.harbor.agents.openclaw:OpenClawAgent"
    assert "--agent-import-path" not in cmd


def test_results_load_openclaw_trajectory(tmp_path: Path):
    from agent_eval.harbor.results import load_openclaw_trajectory_events

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "openclaw-trajectory.jsonl").write_text(
        json.dumps({"type": "tool_call", "name": "exec"}) + "\n"
        + json.dumps({"type": "message", "role": "assistant", "content": "hi"})
        + "\n",
        encoding="utf-8",
    )
    events = load_openclaw_trajectory_events(agent_dir)
    assert isinstance(events, list)
    assert len(events) >= 1


def test_podman_forwards_forge_env():
    """Podman `_FORWARD_ENV` includes Forge secrets (avoid importing Harbor)."""
    import ast

    src = Path(__file__).resolve().parents[1] / "agent_eval" / "harbor" / "podman.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    forwarded: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_FORWARD_ENV":
                    forwarded = {
                        elt.s if hasattr(elt, "s") else elt.value
                        for elt in node.value.elts  # type: ignore[attr-defined]
                    }
    for key in (
        "M365_ACCESS_TOKEN",
        "M365_USER",
        "OPENCLAW_INFERENCE_BASE_URL",
        "SLACK_BOT_TOKEN",
    ):
        assert key in forwarded


def test_openclaw_agent_name():
    from agent_eval.harbor.agents.openclaw import OpenClawAgent

    assert OpenClawAgent.name() == "openclaw"

def test_harbor_tasks_copy_annotations(tmp_path):
    """Harbor environment/ must include annotations.yaml for in-container LLM judges."""
    from agent_eval.config import EvalConfig
    from agent_eval.harbor import tasks as harbor_tasks

    cases = tmp_path / "cases"
    case = cases / "morning-briefing"
    case.mkdir(parents=True)
    (case / "input.yaml").write_text(yaml.safe_dump({"prompt": "brief me"}))
    (case / "annotations.yaml").write_text(
        yaml.safe_dump({"expected_first": "Acme deal"})
    )

    raw = {
        "name": "forge-test",
        "execution": {
            "mode": "case",
            "prompt": "{{ input.prompt }}",
        },
        "runner": {"type": "openclaw"},
        "dataset": {"path": "cases", "schema": "x"},
        "outputs": [{"path": "output", "schema": "response"}],
        "judges": [
            {
                "name": "response_received",
                "check": "return len((outputs.get('output_content') or '').strip()) > 0\n",
                "feedback_type": "bool",
            }
        ],
    }
    cfg_path = tmp_path / "eval.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config = EvalConfig.from_yaml(cfg_path)
    out = tmp_path / "tasks"
    generated = harbor_tasks.generate_tasks(
        config, cfg_path, out, image="localhost/agent-eval-openclaw:latest"
    )
    assert len(generated) == 1
    ann = generated[0] / "environment" / "annotations.yaml"
    assert ann.is_file()
    assert yaml.safe_load(ann.read_text())["expected_first"] == "Acme deal"


def test_score_loads_workspace_annotations(tmp_path):
    """Harbor reward path: annotations.yaml lives in case_dir when dataset is absent."""
    import importlib.util
    from agent_eval.config import EvalConfig

    case = tmp_path / "workspace"
    case.mkdir()
    (case / "annotations.yaml").write_text(yaml.safe_dump({"expected_first": "Deal"}))
    (case / "output").mkdir()
    (case / "output" / "response.txt").write_text("hello")

    raw = {
        "name": "t",
        "execution": {"mode": "case", "prompt": "p"},
        "runner": {"type": "openclaw"},
        "dataset": {"path": "missing-cases", "schema": "x"},
        "outputs": [{"path": "output", "schema": "r"}],
        "judges": [],
    }
    cfg_path = tmp_path / "eval.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    config = EvalConfig.from_yaml(cfg_path)

    score_path = Path("skills/eval-run/scripts/score.py")
    spec = importlib.util.spec_from_file_location("score_mod", score_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rec = mod.load_case_record(case, config)
    assert rec["annotations"].get("expected_first") == "Deal"
