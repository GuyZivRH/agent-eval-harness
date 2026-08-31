"""Test seed_crabline_for_scene handles a list of seeds."""
import pytest
from unittest.mock import patch


def test_seed_crabline_for_scene_empty_list():
    from agent_eval.openshell.crabline_seed import seed_crabline_for_scene
    result = seed_crabline_for_scene([])
    assert result == []


def test_seed_crabline_for_scene_channel_posts():
    from agent_eval.openshell.crabline_seed import seed_crabline_for_scene
    seeds = [
        {"channel": "C001", "text": "Hello channel"},
        {"channel": "C002", "text": "Another message"},
    ]
    with patch("agent_eval.openshell.crabline_seed._slack_form") as mock_slack:
        mock_slack.return_value = {"ok": True, "ts": "123.456", "message": {"ts": "123.456"}}
        result = seed_crabline_for_scene(
            seeds, api_root="http://test:8787/api/", token="xoxb-test"
        )
    assert len(result) == 2
    # Channel posts go direct to chat.postMessage (no conversations.open)
    assert all(call[0][1] == "chat.postMessage" for call in mock_slack.call_args_list)


def test_seed_crabline_for_scene_dm_posts():
    from agent_eval.openshell.crabline_seed import seed_crabline_for_scene
    seeds = [
        {"users": "U001", "text": "Hello DM"},
    ]
    with patch("agent_eval.openshell.crabline_seed._slack_form") as mock_slack:
        mock_slack.side_effect = [
            {"ok": True, "channel": {"id": "D001"}},  # conversations.open
            {"ok": True, "ts": "123.456"},              # chat.postMessage
        ]
        result = seed_crabline_for_scene(
            seeds, api_root="http://test:8787/api/", token="xoxb-test"
        )
    assert len(result) == 1
    assert result[0]["channel"] == "D001"


def test_seed_crabline_for_scene_missing_text():
    from agent_eval.openshell.crabline_seed import seed_crabline_for_scene
    with pytest.raises(ValueError, match="text is required"):
        seed_crabline_for_scene(
            [{"channel": "C001"}],
            api_root="http://test:8787/api/",
            token="xoxb-test",
        )
