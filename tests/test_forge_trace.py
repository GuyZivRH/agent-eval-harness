import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent_eval.openshell import forge


def test_recursive_trace_requires_child_usage_and_sums_resumed_parent(tmp_path):
    parent = [
        {
            "type": "model.completed",
            "runId": "parent",
            "data": {"usage": {"input": 2, "output": 3}},
        },
        {
            "type": "model.completed",
            "runId": "resume",
            "data": {"usage": {"input": 4, "output": 5}},
        },
        {
            "type": "tool.result",
            "data": {
                "result": {
                    "details": {
                        "status": "accepted",
                        "childSessionKey": "agent:brief-reader:subagent:child",
                        "runId": "child",
                    }
                }
            },
        },
    ]
    parent += [
        {
            "type": "assistant.message",
            "data": {"message": {"responseId": str(n), "usage": e["data"]["usage"]}},
        }
        for n, e in enumerate(parent[:2])
    ]
    (tmp_path / "openclaw-trajectory-events.jsonl").write_text(
        "\n".join(map(json.dumps, parent))
    )
    child = {
        "type": "model.completed",
        "runId": "child",
        "data": {"usage": {"input": 6, "output": 7}},
    }
    sandbox = SimpleNamespace(
        exec=AsyncMock(
            side_effect=[
                SimpleNamespace(return_code=0, stdout="{}"),
                SimpleNamespace(
                    return_code=0,
                    stdout=json.dumps(child)
                    + "\n"
                    + json.dumps(
                        {
                            "type": "assistant.message",
                            "data": {
                                "message": {
                                    "responseId": "child",
                                    "usage": child["data"]["usage"],
                                }
                            },
                        }
                    ),
                ),
            ]
        )
    )
    result = asyncio.run(forge.collect_forge_usage(sandbox, "test", tmp_path, {}))
    assert result["complete"] and result["output"] == 15 and result["runs"] == 3
    assert result["sessions"] == 2
    assert (tmp_path / "usage.json").exists()


def test_missing_child_export_fails_closed(tmp_path):
    parent = {
        "type": "tool.result",
        "data": {
            "result": {
                "details": {
                    "status": "accepted",
                    "childSessionKey": "agent:brief-reader:subagent:child",
                    "runId": "child",
                }
            }
        },
    }
    (tmp_path / "openclaw-trajectory-events.jsonl").write_text(json.dumps(parent))
    sandbox = SimpleNamespace(
        exec=AsyncMock(return_value=SimpleNamespace(return_code=1, stdout=""))
    )
    result = asyncio.run(forge.collect_forge_usage(sandbox, "test", tmp_path, {}))
    assert not result["complete"]
