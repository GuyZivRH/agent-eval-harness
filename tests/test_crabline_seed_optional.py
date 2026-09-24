import pytest
from agent_eval.openshell.crabline_seed import seed_crabline_for_case


def test_cases_without_slack_seed_need_no_slack_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SLACK_BOT_TOKEN', raising=False)
    monkeypatch.delenv('CRABLINE_READY_FILE', raising=False)
    assert seed_crabline_for_case({}) is None


def test_requested_slack_seed_still_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('SLACK_BOT_TOKEN', raising=False)
    monkeypatch.delenv('CRABLINE_READY_FILE', raising=False)
    with pytest.raises(RuntimeError, match='SLACK_BOT_TOKEN'):
        seed_crabline_for_case({'crabline_seed': {'text': 'test'}})
