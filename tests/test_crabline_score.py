"""Unit tests for Crabline recorder AEH scorer."""

import json
from pathlib import Path

import pytest

from agent_eval.openshell.crabline_score import (
    score_accepted_post_message,
    score_post_with_code,
    score_threaded_answer,
)


def _write_recorder(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def test_score_accepted_post_message_hit(tmp_path):
    rec = tmp_path / "slack.jsonl"
    marker = "aeh-crabline-case-001-marker"
    _write_recorder(
        rec,
        [
            {
                "path": "/api/auth.test",
                "accepted": True,
                "body": {},
            },
            {
                "path": "/api/chat.postMessage",
                "accepted": True,
                "body": {"channel": "D000000001", "text": f"hello {marker}"},
            },
        ],
    )
    case_dir = tmp_path / "case-001"
    case_dir.mkdir()
    ok, rationale = score_accepted_post_message(
        outputs={
            "annotations": {"expected_text": marker},
            "case_dir": str(case_dir),
        },
        recorder_path=str(rec),
    )
    assert ok is True
    assert "accepted" in rationale
    hits = (case_dir / "output" / "crabline-hits.jsonl").read_text()
    assert marker in hits


def test_score_accepted_post_message_rejects_unaccepted(tmp_path):
    rec = tmp_path / "slack.jsonl"
    marker = "aeh-crabline-case-001-marker"
    _write_recorder(
        rec,
        [
            {
                "path": "/api/chat.postMessage",
                "accepted": False,
                "body": {"channel": "D_CANARY", "text": marker},
            },
        ],
    )
    ok, rationale = score_accepted_post_message(
        outputs={"annotations": {"expected_text": marker}},
        recorder_path=str(rec),
    )
    assert ok is False
    assert "no accepted" in rationale


def test_score_accepted_post_message_missing_recorder(tmp_path):
    ok, rationale = score_accepted_post_message(
        outputs={"annotations": {"expected_text": "x"}},
        recorder_path=str(tmp_path / "missing.jsonl"),
    )
    assert ok is False
    assert "missing" in rationale


def test_score_post_with_code_requires_both(tmp_path):
    rec = tmp_path / "slack.jsonl"
    _write_recorder(
        rec,
        [
            {
                "path": "/api/chat.postMessage",
                "accepted": True,
                "body": {
                    "channel": "D1",
                    "text": "ORANGE-7 aeh-crabline-agent-case-002-marker",
                },
            },
            {
                "path": "/api/chat.postMessage",
                "accepted": True,
                "body": {"channel": "D1", "text": "aeh-crabline-agent-case-002-marker"},
            },
        ],
    )
    ok, _ = score_post_with_code(
        outputs={
            "annotations": {
                "expected_code": "ORANGE-7",
                "expected_text": "aeh-crabline-agent-case-002-marker",
            }
        },
        recorder_path=str(rec),
    )
    assert ok is True
    ok2, _ = score_post_with_code(
        outputs={
            "annotations": {
                "expected_code": "WRONG",
                "expected_text": "aeh-crabline-agent-case-002-marker",
            }
        },
        recorder_path=str(rec),
    )
    assert ok2 is False


def test_score_threaded_answer(tmp_path):
    rec = tmp_path / "slack.jsonl"
    marker = "aeh-crabline-agent-case-003-marker"
    _write_recorder(
        rec,
        [
            {
                "path": "/api/chat.postMessage",
                "accepted": True,
                "body": {"channel": "D1", "text": f"4 {marker}"},
            },
            {
                "path": "/api/chat.postMessage",
                "accepted": True,
                "body": {
                    "channel": "D1",
                    "thread_ts": "1700000000.000100",
                    "text": f"answer is 4 {marker}",
                },
            },
        ],
    )
    ok, rationale = score_threaded_answer(
        outputs={
            "annotations": {
                "expected_answer": "4",
                "expected_text": marker,
                "expect_thread_reply": True,
            }
        },
        recorder_path=str(rec),
    )
    assert ok is True
    assert "thread_ts" in rationale
