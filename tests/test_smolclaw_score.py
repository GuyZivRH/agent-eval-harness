"""Unit tests for smolclaw Gmail/Calendar AEH scorers."""

from __future__ import annotations

import json
from unittest import mock

from agent_eval.openshell.smolclaw_score import (
    score_calendar_event_with_code,
    score_gmail_message_with_code,
)


def test_score_calendar_event_hit():
    events = {
        "items": [
            {
                "id": "evt1",
                "summary": "follow-up CALENDAR-BLUE-9 aeh-smolclaw-case-004-marker",
                "description": "CALENDAR-BLUE-9 aeh-smolclaw-case-004-marker",
            }
        ]
    }
    outputs = {
        "annotations": {
            "expected_code": "CALENDAR-BLUE-9",
            "expected_text": "aeh-smolclaw-case-004-marker",
            "smolclaw_kind": "calendar",
        }
    }
    with mock.patch(
        "agent_eval.openshell.smolclaw_score._get_json", return_value=events
    ):
        ok, rationale = score_calendar_event_with_code(outputs=outputs)
    assert ok is True
    assert "CALENDAR-BLUE-9" in rationale


def test_score_calendar_event_miss():
    outputs = {
        "annotations": {
            "expected_code": "CALENDAR-BLUE-9",
            "expected_text": "aeh-smolclaw-case-004-marker",
        }
    }
    with mock.patch(
        "agent_eval.openshell.smolclaw_score._get_json",
        return_value={"items": [{"summary": "unrelated", "description": ""}]},
    ):
        ok, rationale = score_calendar_event_with_code(outputs=outputs)
    assert ok is False
    assert "no calendar event" in rationale


def test_score_gmail_message_hit():
    listing = {"messages": [{"id": "m1"}]}
    full = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "MAIL-ORANGE-7 aeh-smolclaw-case-005-marker",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Re: MAIL-ORANGE-7 aeh-smolclaw-case-005-marker"}
            ],
            "body": {},
        },
    }
    outputs = {
        "annotations": {
            "expected_code": "MAIL-ORANGE-7",
            "expected_text": "aeh-smolclaw-case-005-marker",
            "smolclaw_kind": "gmail",
        }
    }

    def _get(url: str):
        if "messages/m1" in url:
            return full
        return listing

    with mock.patch("agent_eval.openshell.smolclaw_score._get_json", side_effect=_get):
        ok, rationale = score_gmail_message_with_code(outputs=outputs)
    assert ok is True
    assert "MAIL-ORANGE-7" in rationale


def test_score_gmail_message_miss():
    outputs = {
        "annotations": {
            "expected_code": "MAIL-ORANGE-7",
            "expected_text": "aeh-smolclaw-case-005-marker",
        }
    }
    with mock.patch(
        "agent_eval.openshell.smolclaw_score._get_json",
        return_value={"messages": []},
    ):
        ok, rationale = score_gmail_message_with_code(outputs=outputs)
    assert ok is False
    assert "no gmail message" in rationale
