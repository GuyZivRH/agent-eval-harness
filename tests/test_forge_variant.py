import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_eval.openshell import forge


def test_image_schema_rejects_fixture_before_execution():
    sandbox = SimpleNamespace(
        exec=AsyncMock(
            return_value=SimpleNamespace(
                return_code=0, stdout='["invalid unavailable reason"]'
            )
        )
    )
    with pytest.raises(ValueError, match="invalid unavailable reason"):
        asyncio.run(
            forge.validate_forge_evidence(
                sandbox, "eval", ".openclaw/tmp/brief.evidence.json"
            )
        )
    args = sandbox.exec.call_args.args[1]
    assert args[:3] == ["node", "--input-type=module", "-e"]
    assert "validateBriefEvidence" in args[3]


def test_image_schema_accepts_valid_fixture():
    sandbox = SimpleNamespace(
        exec=AsyncMock(return_value=SimpleNamespace(return_code=0, stdout="[]"))
    )
    asyncio.run(
        forge.validate_forge_evidence(
            sandbox, "eval", ".openclaw/tmp/brief.evidence.json"
        )
    )


def test_variant_refuses_unverified_or_non_skill_bytes(tmp_path):
    path = tmp_path / "replacement.md"
    path.write_text("skill")
    sandbox = SimpleNamespace(exec=AsyncMock())
    with pytest.raises(ValueError, match="hash"):
        asyncio.run(
            forge.stage_skill_variant(
                sandbox,
                "eval",
                tmp_path,
                {"daily-briefing": {"path": "replacement.md", "sha256": "wrong"}},
            )
        )
    with pytest.raises(ValueError, match="skill"):
        asyncio.run(
            forge.stage_skill_variant(
                sandbox,
                "eval",
                tmp_path,
                {
                    "../../AGENTS.md": {
                        "path": "replacement.md",
                        "sha256": hashlib.sha256(b"skill").hexdigest(),
                    }
                },
            )
        )
    sandbox.exec.assert_not_called()


def test_host_only_random_scaffolding_is_not_uploaded_or_hashed(tmp_path):
    for name in (
        ".git/index",
        ".claude/settings.json",
        "hooks/subagent_stop.py",
        "eval/seed.json",
        "draft-expectations.json",
    ):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("fixture")
    assert {
        p.relative_to(tmp_path).as_posix() for p in forge.forge_input_files(tmp_path)
    } == {"eval/seed.json", "draft-expectations.json"}
