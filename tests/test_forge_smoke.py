import json
from agent_eval.forge_contract import check_child_smoke


def test_parent_fallback_cannot_pass(tmp_path):
    (tmp_path / "openclaw-trajectory-events.jsonl").write_text("")
    assert (
        check_child_smoke(tmp_path, "CHILD_OK_20260924")["status"] == "quality_failed"
    )


def test_child_invocation_success_and_parent_consumption_required(tmp_path):
    key = "agent:brief-reader:child"

    def action(name, path, session):
        return [
            {
                "type": "tool.call",
                "sessionKey": session,
                "data": {
                    "toolCallId": name + path,
                    "name": name,
                    "arguments": {"path": path},
                },
            },
            {
                "type": "tool.result",
                "sessionKey": session,
                "data": {"toolCallId": name + path, "name": name, "success": True},
            },
        ]

    parent = [
        {
            "type": "tool.result",
            "data": {
                "name": "sessions_spawn",
                "result": {"details": {"status": "accepted", "childSessionKey": key}},
            },
        }
    ]
    parent += action("read", "/sandbox/child-output.txt", "parent")
    child = action("read", "/sandbox/child-input.txt", key) + action(
        "write", "/sandbox/child-output.txt", key
    )
    (tmp_path / "openclaw-trajectory-events.jsonl").write_text(
        "\n".join(map(json.dumps, parent))
    )
    (tmp_path / "eval-child-one.jsonl").write_text("\n".join(map(json.dumps, child)))
    assert check_child_smoke(tmp_path, "CHILD_OK_20260924")["status"] == "passed"
    assert check_child_smoke(tmp_path, "not consumed")["status"] == "quality_failed"
