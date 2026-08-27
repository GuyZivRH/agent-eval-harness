"""Test seed_smolclaw_for_scene handles a list of seeds."""
import pytest
from unittest.mock import patch, MagicMock

def test_seed_smolclaw_for_scene_empty_list():
    from agent_eval.openshell.smolclaw_seed import seed_smolclaw_for_scene
    result = seed_smolclaw_for_scene([])
    assert result == []

def test_seed_smolclaw_for_scene_mixed_kinds():
    from agent_eval.openshell.smolclaw_seed import seed_smolclaw_for_scene
    seeds = [
        {"kind": "gmail", "subject": "Test email", "body": "Hello"},
        {"kind": "calendar", "summary": "Test event"},
        {"kind": "gmail", "subject": "Another email", "body": "World"},
    ]
    with patch("agent_eval.openshell.smolclaw_seed._seed_gmail") as mock_gmail, \
         patch("agent_eval.openshell.smolclaw_seed._seed_calendar") as mock_cal:
        mock_gmail.return_value = {"ok": True, "kind": "gmail", "message_id": "m1"}
        mock_cal.return_value = {"ok": True, "kind": "calendar", "event_id": "e1"}
        result = seed_smolclaw_for_scene(seeds)
    assert len(result) == 3
    assert mock_gmail.call_count == 2
    assert mock_cal.call_count == 1

def test_seed_smolclaw_for_scene_invalid_kind():
    from agent_eval.openshell.smolclaw_seed import seed_smolclaw_for_scene
    with pytest.raises(ValueError, match="gmail|calendar"):
        seed_smolclaw_for_scene([{"kind": "sms", "subject": "bad"}])
