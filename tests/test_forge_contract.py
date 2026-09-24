import hashlib
import importlib
import json
from pathlib import Path

import pytest


def api():
    try:
        return importlib.import_module("agent_eval.forge_contract")
    except ModuleNotFoundError:
        pytest.fail("Forge artifact/fixture validation is missing")


def fixture(root):
    evidence = {
        "evidenceId": "test-1",
        "sealed": True,
        "collectedAt": "2026-09-24T12:00:00Z",
        "window": {"sinceIso": "2026-09-21T12:00:00Z"},
        "microsoft365": {
            "messages": [
                {"id": "decision", "receivedDateTime": "2026-09-24T11:00:00Z"}
            ],
            "events": [],
        },
        "slack": {"messages": []},
    }
    data = json.dumps(evidence).encode()
    (root / "evidence.json").write_bytes(data)
    manifest = {
        "version": 1,
        "as_of": "2026-09-24T12:00:00Z",
        "evidence": "evidence.json",
        "sha256": hashlib.sha256(data).hexdigest(),
        "expected": {"topOfMind": ["m365:decision"]},
    }
    (root / "eval-fixture.json").write_text(json.dumps(manifest))
    return manifest


def test_fixture_rejects_annotations_not_in_evidence(tmp_path):
    m = fixture(tmp_path)
    m["expected"]["topOfMind"] = ["m365:missing"]
    (tmp_path / "eval-fixture.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="missing"):
        api().validate_fixture(tmp_path)


def test_fixture_rejects_hash_drift_and_path_escape(tmp_path):
    m = fixture(tmp_path)
    (tmp_path / "evidence.json").write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        api().validate_fixture(tmp_path)
    m["evidence"] = "../secret"
    (tmp_path / "eval-fixture.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="path"):
        api().validate_fixture(tmp_path)


def test_fixture_requires_in_window_source(tmp_path):
    fixture(tmp_path)
    d = json.loads((tmp_path / "evidence.json").read_text())
    d["microsoft365"]["messages"][0]["receivedDateTime"] = "2026-01-01T00:00:00Z"
    data = json.dumps(d).encode()
    (tmp_path / "evidence.json").write_bytes(data)
    m = json.loads((tmp_path / "eval-fixture.json").read_text())
    m["sha256"] = hashlib.sha256(data).hexdigest()
    (tmp_path / "eval-fixture.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="window"):
        api().validate_fixture(tmp_path)


def test_equivalent_action_annotations_cannot_name_missing_evidence(tmp_path):
    m = fixture(tmp_path)
    m["equivalent_actions"] = [["m365:decision", "m365:absent"]]
    (tmp_path / "eval-fixture.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="missing"):
        api().validate_fixture(tmp_path)


def test_canonical_brief_required_even_if_chat_claims_publication(tmp_path):
    fixture(tmp_path)
    verdict = api().check_brief(tmp_path)
    assert verdict["status"] == "quality_failed"
    assert "missing brief.json" in verdict["issues"]


def test_brief_rejects_invented_citation_and_missing_required_item(tmp_path):
    fixture(tmp_path)
    (tmp_path / "brief.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "evidenceId": "test-1",
                "scope": "full",
                "topOfMind": [{"id": "m365:invented"}],
                "fyi": [],
                "lookingAhead": [],
            }
        )
    )
    issues = api().check_brief(tmp_path)["issues"]
    assert any("invented" in s for s in issues)
    assert any("decision" in s for s in issues)


def test_usage_reconciles_all_runs_without_double_counting_reasoning():
    rows = [
        {
            "type": "model.completed",
            "runId": "parent",
            "data": {"usage": {"input": 20, "output": 10, "reasoningTokens": 7}},
        },
        {
            "type": "model.completed",
            "runId": "child",
            "data": {"usage": {"input": 5, "output": 3, "reasoningTokens": 2}},
        },
    ]
    result = api().account_usage(rows, expected_runs={"parent", "child"})
    assert result["complete"] is True
    assert result["output"] == 13
    assert result["reasoning"] == 9
    assert (
        api().account_usage(rows[:1], expected_runs={"parent", "child"})["complete"]
        is False
    )


def test_usage_missing_field_is_not_zero_or_an_estimate():
    rows = [
        {"type": "model.completed", "runId": "parent", "data": {"usage": {"input": 20}}}
    ]
    assert api().account_usage(rows, expected_runs={"parent"})["complete"] is False


def test_publication_must_match_composer_hash_and_coverage(tmp_path):
    fixture(tmp_path)
    brief = {
        "schemaVersion": 1,
        "evidenceId": "test-1",
        "scope": "full",
        "generatedAt": "2026-09-24T12:00:00Z",
        "topOfMind": [{"id": "m365:decision"}],
        "fyi": [],
        "lookingAhead": [],
        "coverage": [],
    }
    data = json.dumps(brief).encode()
    (tmp_path / "brief.json").write_bytes(data)
    assert any("provenance" in s for s in api().check_brief(tmp_path)["issues"])
    stamp = tmp_path / ".openclaw/tmp/brief.candidate.provenance.json"
    stamp.parent.mkdir(parents=True)
    stamp.write_text(
        json.dumps({"schemaVersion": 1, "sha256": hashlib.sha256(data).hexdigest()})
    )
    assert not any("provenance" in s for s in api().check_brief(tmp_path)["issues"])
    (tmp_path / "brief.json").write_bytes(data + b" ")
    assert any("provenance" in s for s in api().check_brief(tmp_path)["issues"])
