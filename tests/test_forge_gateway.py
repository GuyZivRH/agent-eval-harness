import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import agent_eval.openshell.forge as forge


def test_gateway_invocation_uses_isolated_wrapper_and_forwards_errors(tmp_path):
    run = getattr(forge, "run_forge_gateway", None)
    assert callable(run), "gateway execution must not fall back to agent exec"
    sandbox = SimpleNamespace(
        exec=AsyncMock(
            side_effect=[
                SimpleNamespace(return_code=0, stdout="", stderr=""),
                SimpleNamespace(return_code=0, stdout="", stderr=""),
                SimpleNamespace(
                    return_code=7, stdout="", stderr="gateway startup failed"
                ),
            ]
        )
    )
    result = asyncio.run(
        run(
            sandbox,
            "eval-only",
            "Read a batch",
            env={
                "OPENCLAW_CONFIG_PATH": "/sandbox/openclaw-eval.json",
            },
            timeout_s=90,
            effort="high",
            case_id="child-smoke",
        )
    )
    assert result.return_code == 7
    argv = sandbox.exec.call_args.args[1]
    assert argv[:2] == ["node", "/sandbox/.openclaw/eval-gateway.mjs"]
    assert "--deliver" not in argv
    assert sandbox.exec.call_args.kwargs["env"]["AGENT_EVAL_CASE_ID"] == "child-smoke"


def test_gateway_rejects_unsafe_case_identifier():
    run = getattr(forge, "run_forge_gateway", None)
    assert callable(run)
    import pytest

    with pytest.raises(ValueError, match="case id"):
        asyncio.run(
            run(
                None,
                "eval-only",
                "hi",
                env={},
                timeout_s=30,
                effort="high",
                case_id="../live",
            )
        )
