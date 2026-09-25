from agent_eval import forge_contract


def test_duplicate_changed_wording_cannot_pass_draft_gate():
    row = {
        "draft_id": "one",
        "state": "proposed",
        "payload": {
            "channel": "mail",
            "context_ref": "source",
            "to": ["a@example.test"],
        },
        "versions": [{"version_id": "v1"}],
    }
    snapshot = {
        "before": [row],
        "after": [row, dict(row, draft_id="two")],
        "ledger": [],
    }
    result = forge_contract.check_drafts(snapshot, {"count": 1, "unchanged": ["one"]})
    assert result["status"] == "quality_failed"
    assert any("duplicate" in s for s in result["issues"])


def test_accepted_from_other_session_must_not_reopen():
    before = {
        "draft_id": "one",
        "state": "accepted",
        "payload": {},
        "versions": [{"version_id": "v1"}],
    }
    result = forge_contract.check_drafts(
        {"before": [before], "after": [dict(before, state="proposed")], "ledger": []},
        {"count": 1, "unchanged": ["one"]},
    )
    assert result["status"] == "quality_failed"


def test_revision_must_include_requested_content_and_no_send_attempt():
    old = {
        "draft_id": "one",
        "state": "proposed",
        "payload": {"body": "Old wording"},
        "versions": [{}],
    }
    new = {**old, "payload": {"body": "Done"}, "versions": [{}, {}]}
    expected = {
        "count": 1,
        "revised": ["one"],
        "content": [
            {"match": {}, "body_patterns": ["API rate limits", "September 28"]}
        ],
    }
    result = forge_contract.check_drafts(
        {
            "before": [old],
            "after": [new],
            "ledger": [{"method": "POST", "target": "/drafts/one/send"}],
        },
        expected,
    )
    assert any("requested content" in s for s in result["issues"])
    assert any("unauthorized" in s for s in result["issues"])


def test_real_cli_version_route_and_content_pass_but_withdrawal_fails():
    old = {
        "draft_id": "one",
        "state": "proposed",
        "payload": {"body": "Old wording"},
        "versions": [{}],
    }
    new = {
        **old,
        "payload": {"body": "Feedback on API rate limits before September 28"},
        "versions": [{}, {}],
    }
    snapshot = {
        "before": [old],
        "after": [new],
        "ledger": [{"method": "POST", "target": "/drafts/one/versions"}],
    }
    expected = {
        "count": 1,
        "revised": ["one"],
        "content": [
            {"match": {}, "body_patterns": ["API rate limits", "September 28"]}
        ],
    }
    assert forge_contract.check_drafts(snapshot, expected)["status"] == "passed"
    new["state"] = "withdrawn"
    assert forge_contract.check_drafts(snapshot, expected)["status"] == "quality_failed"
